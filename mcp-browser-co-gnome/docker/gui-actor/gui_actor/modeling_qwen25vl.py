import torch
import torch.nn as nn
import torch.nn.functional as F

from typing import List, Tuple, Union, Optional

from transformers.models.qwen2_5_vl.modeling_qwen2_5_vl import (
    Qwen2_5_VLCausalLMOutputWithPast,
    Qwen2_5_VLForConditionalGeneration,
)
from gui_actor.constants import IGNORE_INDEX
from gui_actor.trainer import rank0_print


def _get_token_embedding_layer(hf_model: nn.Module) -> nn.Module:
    """
    Robustly locate the token embedding layer across HF versions.
    """
    if hasattr(hf_model, "get_input_embeddings") and callable(hf_model.get_input_embeddings):
        return hf_model.get_input_embeddings()
    # Fallbacks (shouldn't be needed on recent transformers, but safe to keep)
    lm = getattr(hf_model, "language_model", None)
    if lm is not None and hasattr(lm, "embed_tokens"):
        return lm.embed_tokens
    raise AttributeError("Could not locate token embedding layer on model (no get_input_embeddings/embed_tokens).")


class QwenVLwithVisionHeadOutputWithPast(Qwen2_5_VLCausalLMOutputWithPast):
    """
    Output class for Qwen2_5_VL with pointer head, extending the base output class.

    Args:
        lm_loss (`torch.FloatTensor` of shape `(1,)`, *optional*):
            Language modeling loss.
        pointer_loss (`torch.FloatTensor` of shape `(1,)`, *optional*):
            Vision pointer network loss.
        pointer_scores (`List[torch.FloatTensor]`, *optional*):
            Attention scores from the pointer network, one tensor per batch item.
        loss (`torch.FloatTensor` of shape `(1,)`, *optional*):
            Combined loss (weighted sum of lm_loss and pointer_loss).
        logits (`torch.FloatTensor` of shape `(batch_size, sequence_length, config.vocab_size)`):
            Prediction scores from the language modeling head.
        past_key_values, hidden_states, attentions, rope_deltas:
            Same as parent class.
    """
    def __init__(self, lm_loss=None, pointer_loss=None, pointer_scores=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lm_loss = lm_loss
        self.pointer_loss = pointer_loss
        self.pointer_scores = pointer_scores


class VisionHead_MultiPatch(nn.Module):
    def __init__(self, d_model, projection_dim, num_attention_heads=8, dropout_rate=0.1):
        super().__init__()
        self.d_model = d_model

        self.projection_enc = nn.Sequential(
            nn.Linear(d_model, projection_dim),
            nn.GELU(),
            nn.Linear(projection_dim, d_model),
        )
        self.projection_dec = nn.Sequential(
            nn.Linear(d_model, projection_dim),
            nn.GELU(),
            nn.Linear(projection_dim, d_model),
        )

        self.self_attention = nn.MultiheadAttention(
            embed_dim=d_model, num_heads=num_attention_heads, dropout=dropout_rate, batch_first=True
        )

        self.layer_norm = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout_rate)

    def forward(
        self,
        hidden_state_enc,  # [n_enc, d_model]
        hidden_state_dec,  # [n_dec, d_model]
        labels: Optional[torch.Tensor] = None,  # [n_dec, n_enc] binary mask of patches in bbox
        do_single_patch: bool = False,
    ):
        enc_input = hidden_state_enc.unsqueeze(0)
        attn_output, _ = self.self_attention(query=enc_input, key=enc_input, value=enc_input, need_weights=False)
        hidden_state_enc_ctx = self.layer_norm(enc_input + self.dropout(attn_output)).squeeze(0)  # [n_enc, d_model]

        proj_enc = self.projection_enc(hidden_state_enc_ctx)  # [n_enc, d_model]
        proj_dec = self.projection_dec(hidden_state_dec)      # [n_dec, d_model]

        scaling = self.d_model ** 0.5
        patch_logits = torch.matmul(proj_dec, proj_enc.transpose(0, 1)) / scaling  # [n_dec, n_enc]

        attn_weights = F.softmax(patch_logits, dim=-1)

        loss = None
        if (labels is not None) and (not do_single_patch):
            epsilon = 1e-8
            labels_float = labels.float()
            target_dist = labels_float / (labels_float.sum(dim=-1, keepdim=True) + epsilon)
            pred_log_probs = F.log_softmax(patch_logits, dim=-1)
            loss = F.kl_div(pred_log_probs, target_dist, reduction='batchmean')

        if do_single_patch and (labels is not None):
            # NOTE: if you ever enable this branch, use patch_logits for CE
            loss = F.cross_entropy(patch_logits, labels)

        return attn_weights, loss


class Qwen2_5_VLForConditionalGenerationWithPointer(Qwen2_5_VLForConditionalGeneration):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.multi_patch_pointer_head = VisionHead_MultiPatch(self.config.hidden_size, self.config.hidden_size)
        self.pointer_loss_weight = kwargs.get("pointer_loss_weight", 1.0)
        self.lm_loss_weight = kwargs.get("lm_loss_weight", 1.0)
        self.post_init()

        # init rope cache slot (used in return_dict path)
        self.rope_deltas = None

    def reset_loss_weights(self, pointer_loss_weight, lm_loss_weight):
        self.pointer_loss_weight = pointer_loss_weight
        self.lm_loss_weight = lm_loss_weight

    def forward(
        self,
        input_ids: torch.LongTensor = None,  # (batch_size, seq_len)
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_values: Optional[List[torch.FloatTensor]] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        labels: Optional[torch.LongTensor] = None,
        use_cache: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
        pixel_values: Optional[torch.Tensor] = None,
        pixel_values_videos: Optional[torch.FloatTensor] = None,
        image_grid_thw: Optional[torch.LongTensor] = None,
        video_grid_thw: Optional[torch.LongTensor] = None,
        rope_deltas: Optional[torch.LongTensor] = None,
        cache_position: Optional[torch.LongTensor] = None,
        second_per_grid_ts: Optional[torch.Tensor] = None,
        # Grounding
        visual_token_indices_of_coordinates: Optional[torch.Tensor] = None,  # (batch_size, n_target)
        multi_patch_labels: Optional[torch.Tensor] = None,                   # list/packed: [(n_target, n_visual), ...]
        if_multi_patch: bool = True,
        coordinates: Optional[List[Tuple[float, float]]] = None,
        verbose: bool = False,
    ) -> Union[Tuple, QwenVLwithVisionHeadOutputWithPast]:

        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        if verbose:
            rank0_print(f"input_ids: {None if input_ids is None else (input_ids.shape, input_ids[0][:5])}")
            rank0_print(f"labels: {None if labels is None else (labels.shape, labels[0][:5])}")
            rank0_print(f"pixel_values: {None if pixel_values is None else pixel_values.shape}")
            rank0_print(f"image_grid_thw: {None if image_grid_thw is None else image_grid_thw.shape}")
            rank0_print(f"coordinates: {coordinates}")
            rank0_print(f"visual_token_indices_of_coordinates: {visual_token_indices_of_coordinates}")
            rank0_print(f"return_dict: {return_dict}")

        if inputs_embeds is None:
            if input_ids is None:
                raise ValueError("Either inputs_embeds or input_ids must be provided.")

            # FIX: use embedding accessor instead of .embed_tokens
            token_embedding = _get_token_embedding_layer(self.model)
            inputs_embeds = token_embedding(input_ids)  # (batch, seq_len, d_model)

            if pixel_values is not None:
                pixel_values = pixel_values.type(self.visual.dtype)
                image_embeds = self.visual(pixel_values, grid_thw=image_grid_thw)
                n_image_tokens = (input_ids == self.config.image_token_id).sum().item()
                n_image_features = image_embeds.shape[0]
                if n_image_tokens != n_image_features:
                    raise ValueError(
                        f"Image features and image tokens do not match: tokens: {n_image_tokens}, features: {n_image_features}"
                    )
                image_mask = (
                    (input_ids == self.config.image_token_id)
                    .unsqueeze(-1)
                    .expand_as(inputs_embeds)
                    .to(inputs_embeds.device)
                )
                image_embeds = image_embeds.to(inputs_embeds.device, inputs_embeds.dtype)
                inputs_embeds = inputs_embeds.masked_scatter(image_mask, image_embeds)

            if pixel_values_videos is not None:
                pixel_values_videos = pixel_values_videos.type(self.visual.dtype)
                video_embeds = self.visual(pixel_values_videos, grid_thw=video_grid_thw)
                n_video_tokens = (input_ids == self.config.video_token_id).sum().item()
                n_video_features = video_embeds.shape[0]
                if n_video_tokens != n_video_features:
                    raise ValueError(
                        f"Video features and video tokens do not match: tokens: {n_video_tokens}, features: {n_video_features}"
                    )
                video_mask = (
                    (input_ids == self.config.video_token_id)
                    .unsqueeze(-1)
                    .expand_as(inputs_embeds)
                    .to(inputs_embeds.device)
                )
                video_embeds = video_embeds.to(inputs_embeds.device, inputs_embeds.dtype)
                inputs_embeds = inputs_embeds.masked_scatter(video_mask, video_embeds)

            if attention_mask is not None:
                attention_mask = attention_mask.to(inputs_embeds.device)

        # RoPE positions / deltas
        if position_ids is None and (attention_mask is None or attention_mask.ndim == 2):
            if (
                (cache_position is not None and cache_position[0] == 0)
                or self.rope_deltas is None
                or (past_key_values is None or past_key_values.get_seq_length() == 0)
            ):
                position_ids, rope_deltas = self.get_rope_index(input_ids, image_grid_thw, video_grid_thw, attention_mask)
                self.rope_deltas = rope_deltas
            else:
                batch_size, seq_length, _ = inputs_embeds.shape
                delta = cache_position[0] + self.rope_deltas if cache_position is not None else 0
                position_ids = torch.arange(seq_length, device=inputs_embeds.device)
                position_ids = position_ids.view(1, -1).expand(batch_size, -1)
                if cache_position is not None:
                    delta = delta.repeat_interleave(batch_size // delta.shape[0], dim=0).to(position_ids.device)
                position_ids = position_ids.add(delta)
                position_ids = position_ids.unsqueeze(0).expand(3, -1, -1)

        outputs = self.model(
            input_ids=None,
            position_ids=position_ids,
            attention_mask=attention_mask,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            use_cache=use_cache,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
            cache_position=cache_position,
        )

        hidden_states = outputs[0]  # (batch, seq_len, d_model)
        logits = self.lm_head(hidden_states)

        lm_loss = None
        if labels is not None and self.lm_loss_weight > 0:
            logits = logits.float()
            shift_logits = logits[..., :-1, :].contiguous()
            shift_labels = labels[..., 1:].contiguous()
            loss_fct = nn.CrossEntropyLoss()
            shift_logits = shift_logits.view(-1, self.config.vocab_size)
            shift_labels = shift_labels.view(-1).to(shift_logits.device)
            lm_loss = loss_fct(shift_logits, shift_labels)

        pointer_loss = None
        pointer_scores = []
        if visual_token_indices_of_coordinates is not None:
            batch_size = input_ids.shape[0]
            pointer_losses = []

            for i in range(batch_size):
                dummy_target = False

                token_ids = input_ids[i]      # (seq_len,)
                hs = hidden_states[i]         # (seq_len, d_model)

                visual_mask = (token_ids == self.config.image_token_id)
                visual_indices = torch.nonzero(visual_mask, as_tuple=False).squeeze(-1)  # (n_visual,)

                target_mask = (token_ids == self.config.pointer_pad_token_id)
                target_indices = torch.nonzero(target_mask, as_tuple=False).squeeze(-1)

                if visual_indices.numel() == 0:
                    raise ValueError(f"No visual tokens found for sample {i}.")

                if target_indices.numel() == 0:
                    target_indices = torch.tensor([hs.shape[0] - 1], device=hs.device)
                    gt = torch.tensor([0], device=hs.device)  # not used in multi-patch
                    if if_multi_patch:
                        sample_labels = torch.zeros_like(visual_indices).unsqueeze(0)
                        sample_labels[0][:4] = 1
                    dummy_target = True
                else:
                    gt = visual_token_indices_of_coordinates[i].to(hs.device)  # (n_target,)
                    if if_multi_patch:
                        sample_labels = multi_patch_labels[i]

                # Use input embeddings for visual tokens (image tokens got replaced earlier)
                visual_embeds = inputs_embeds[i][visual_indices]  # (n_visual, d_model)
                target_hidden = hs[target_indices]                # (n_target, d_model)

                if if_multi_patch:
                    if sample_labels.shape[0] != target_indices.shape[0]:
                        raise ValueError(
                            f"Sample {i} mismatched targets: {sample_labels.shape[0]} labels vs {target_indices.shape[0]} targets"
                        )
                    attn_scores, loss_v = self.multi_patch_pointer_head(
                        visual_embeds,
                        target_hidden,
                        labels=sample_labels,
                    )
                else:
                    # Deprecated: single-patch branch
                    attn_scores, loss_v = self.pointer_head(visual_embeds, target_hidden, labels=gt)

                pointer_scores.append(attn_scores.detach().cpu())
                pointer_losses.append(loss_v * 0.0 if dummy_target else loss_v)

            pointer_loss = torch.stack(pointer_losses).mean()

        if lm_loss is None:
            total_loss = pointer_loss
        elif pointer_loss is None:
            total_loss = lm_loss
        else:
            total_loss = self.lm_loss_weight * lm_loss + self.pointer_loss_weight * pointer_loss

        if return_dict:
            return QwenVLwithVisionHeadOutputWithPast(
                lm_loss=lm_loss,
                pointer_loss=pointer_loss,
                pointer_scores=pointer_scores,
                loss=total_loss,
                logits=logits,
                past_key_values=outputs.past_key_values,
                hidden_states=outputs.hidden_states,
                attentions=outputs.attentions,
                rope_deltas=self.rope_deltas,
            )
        else:
            if labels is not None:
                output = (lm_loss, pointer_loss, logits, pointer_scores,) + outputs[1:]
                return (total_loss,) + output if total_loss is not None else output
            else:
                return outputs
