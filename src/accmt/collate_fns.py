import numpy as np
import torch
from collections import defaultdict

class DataCollatorForSeq2Seq:
    """
    Automatically adds efficient padding for inputs and labels.

    When called, returns a dictionary with the following keys:
        - `input_ids`
        - `attention_mask`
        - `labels`

    This implementation derives from `transformers` library:
    https://github.com/huggingface/transformers/blob/main/src/transformers/data/data_collator.py#L543
    """
    def __init__(self, tokenizer, label_pad_token_id=-100):
        self.tokenizer = tokenizer
        self.pad_token_id = self.tokenizer.pad_token_id
        self.label_pad_token_id = label_pad_token_id
        self.padding_side = self.tokenizer.padding_side

    def __call__(self, batch: list) -> dict:
        inputs = []
        labels = []
        for feature in batch:
            inputs.append(feature["input_ids"])
            labels.append(feature["labels"])

        max_label_length = max(len(l) for l in labels)
        max_input_length = max(len(l) for l in inputs)

        inputs = []
        attention_masks = []
        labels = []
        for feature in batch:
            inputs_remainder = [self.pad_token_id] * (max_input_length - len(feature["input_ids"]))
            attention_masks_remainder = [0] * (max_input_length - len(feature["input_ids"]))
            labels_remainder = [self.label_pad_token_id] * (max_label_length - len(feature["labels"]))
            
            if isinstance(feature["labels"], list):
                feature = {
                    "input_ids": feature["input_ids"] + inputs_remainder,
                    "attention_mask": feature["attention_mask"] + attention_masks_remainder,
                    "labels": feature["labels"] + labels_remainder
                }
            elif self.padding_side == "right":
                feature = {
                    "input_ids": np.concatenate([feature["input_ids"], inputs_remainder]).astype(np.int64),
                    "attention_mask": np.concatenate([feature["attention_mask"], attention_masks_remainder]).astype(np.int64),
                    "labels": np.concatenate([feature["labels"], labels_remainder]).astype(np.int64)
                }
            else:
                feature = {
                    "input_ids": np.concatenate([inputs_remainder, feature["input_ids"]]).astype(np.int64),
                    "attention_mask": np.concatenate([attention_masks_remainder, feature["attention_mask"]]).astype(np.int64),
                    "labels": np.concatenate([labels_remainder, feature["labels"]]).astype(np.int64)
                }

            inputs.append(feature["input_ids"])
            attention_masks.append(feature["attention_mask"])
            labels.append(feature["labels"])
    
        return {
            "input_ids": torch.from_numpy(np.stack(inputs)),
            "attention_mask": torch.from_numpy(np.stack(attention_masks)),
            "labels": torch.from_numpy(np.stack(labels))
        }

class DataCollatorForLongestSequence:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.pad_token_id = self.tokenizer.pad_token_id
        self.padding_side = self.tokenizer.padding_side

    def __call__(self, batch: list) -> dict:
        inputs = []
        for feature in batch:
            # if feature is a tuple, then it would be of type (inputs, targets)
            if isinstance(feature, tuple): feature = feature[0] # just take first element
            inputs.append(feature["input_ids"])

        max_input_length = max(len(l) for l in inputs)

        inputs = []
        attention_masks = []
        labels = []
        for feature in batch:
            if isinstance(feature, tuple):
                labels.append(feature[1])
                feature = feature[0]
            inputs_remainder = [self.pad_token_id] * (max_input_length - len(feature["input_ids"]))
            attention_masks_remainder = [0] * (max_input_length - len(feature["input_ids"]))
            
            if self.padding_side == "right":
                feature = {
                    "input_ids": np.concatenate([feature["input_ids"], inputs_remainder]).astype(np.int64),
                    "attention_mask": np.concatenate([feature["attention_mask"], attention_masks_remainder]).astype(np.int64)
                }
            else:
                feature = {
                    "input_ids": np.concatenate([inputs_remainder, feature["input_ids"]]).astype(np.int64),
                    "attention_mask": np.concatenate([attention_masks_remainder, feature["attention_mask"]]).astype(np.int64)
                }

            inputs.append(feature["input_ids"])
            attention_masks.append(feature["attention_mask"])

        output = {
            "input_ids": torch.from_numpy(np.stack(inputs)),
            "attention_mask": torch.from_numpy(np.stack(attention_masks))
        }

        if len(labels) > 0:
            return (output, torch.stack(labels))
            
        return output

class DataCollatorForLanguageModeling:
    def __init__(self,
                 tokenizer,
                 mlm=True,
                 mlm_probability=0.15,
                 ignore_index=-100,
                 masked_to_mask=0.8,
                 apply_random_words=True,
                 keep_original_input=False
    ):
        self.tokenizer = tokenizer
        self.mlm = mlm
        self.mlm_probability = mlm_probability
        self.ignore_index = ignore_index
        self.masked_to_mask = masked_to_mask
        self.apply_random_words = apply_random_words
        self.keep_original_input = keep_original_input

    def __call__(self, batch: list) -> dict:
        original_input_list = []
        input_list = []
        attention_mask_list = []
        label_list = []
        extra_targets = defaultdict(list)
        for feature in batch:
            if self.keep_original_input:
                original_input_list.append(_feature["input_ids"].clone())
            if isinstance(feature, tuple):
                _feature = feature[0]
                if isinstance(feature[1], dict):
                    for k, v in feature[1].items():
                        extra_targets[k].append(v)
            else:
                _feature = feature
            inputs = _feature["input_ids"]
            special_tokens_mask = _feature.pop("special_tokens_mask", None)
            # specials tokens can be [CLS], [SEP], [PAD] or related
            if self.mlm:
                labels = _feature["input_ids"].clone()
                probability_matrix = torch.full(labels.shape, self.mlm_probability)
                if special_tokens_mask is None:
                    special_tokens_mask = self.tokenizer.get_special_tokens_mask(labels.tolist(), already_has_special_tokens=True)
                    special_tokens_mask = torch.tensor(special_tokens_mask, dtype=torch.bool)
                else:
                    special_tokens_mask = special_tokens_mask.bool()

                probability_matrix.masked_fill_(special_tokens_mask, value=0.0)
                masked_indices = torch.bernoulli(probability_matrix).bool()
                labels[~masked_indices] = self.ignore_index # only compute loss on masked tokens

                if isinstance(self.masked_to_mask, float) and self.masked_to_mask != 0.0:
                    indices_replaced = torch.bernoulli(torch.full(labels.shape, self.masked_to_mask)).bool() & masked_indices
                    inputs[indices_replaced] = self.tokenizer.convert_tokens_to_ids(self.tokenizer.mask_token)

                    if self.apply_random_words:
                        indices_random = torch.bernoulli(torch.full(labels.shape, 0.5)).bool() & masked_indices & ~indices_replaced
                        random_words = torch.randint(len(self.tokenizer), labels.shape, dtype=torch.long)
                        inputs[indices_random] = random_words[indices_random]

                _feature["labels"] = labels
            else:
                labels = _feature["input_ids"].clone()
                if self.tokenizer.pad_token_id is not None:
                    labels[labels == self.tokenizer.pad_token_id] = -100
                _feature["labels"] = labels

            input_list.append(inputs)
            attention_mask_list.append(_feature["attention_mask"])
            label_list.append(labels)

        output = {
            "input_ids": torch.stack(input_list),
            "attention_mask": torch.stack(attention_mask_list),
            "labels": torch.stack(label_list)
        }

        if self.keep_original_input:
            output["unmasked_input_ids"] = torch.stack(original_input_list)

        if len(extra_targets) > 0:
            extra_targets = dict(extra_targets)
            for k, v in extra_targets.items():
                extra_targets[k] = torch.stack(v)

            return output, extra_targets
        else:
            return output
