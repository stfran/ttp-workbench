#!/usr/bin/env python3
"""
secbert_train.py — fine-tune a SecBERT classifier head and save artifacts

Follows the structure of the Jupyter notebook but adapted to a script
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import AutoTokenizer, AutoModel, BertConfig, get_linear_schedule_with_warmup
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# ----------------------- Model & Dataset (faithful to notebook) -----------------------

class Triage(Dataset):
    def __init__(self, dataframe: pd.DataFrame, tokenizer, max_len: int):
        self.len = len(dataframe)
        self.data = dataframe.reset_index(drop=True)
        self.tokenizer = tokenizer
        self.max_len = max_len
    def __getitem__(self, index):
        sentence = str(self.data.sentence.iloc[index])
        sentence = " ".join(sentence.split())
        inputs = self.tokenizer.encode_plus(
            sentence,
            None,
            add_special_tokens=True,
            max_length=self.max_len,
            padding='max_length',
            truncation=True,
            return_token_type_ids=True,
        )
        item = {
            'ids': torch.tensor(inputs['input_ids'], dtype=torch.long),
            'mask': torch.tensor(inputs['attention_mask'], dtype=torch.long),
        }
        if 'enc_label' in self.data.columns:
            item['targets'] = torch.tensor(int(self.data.enc_label.iloc[index]), dtype=torch.long)
        return item
    def __len__(self):
        return self.len

class SecBERTClass(torch.nn.Module):
    def __init__(self, pretrained_model_name: str, num_classes: int, dropout: float = 0.3):
        super().__init__()
        config = BertConfig.from_pretrained(pretrained_model_name, output_hidden_states=True)
        self.model = AutoModel.from_pretrained(pretrained_model_name, config=config).base_model
        self.pre_classifier = torch.nn.Linear(768, 768)
        self.dropout = torch.nn.Dropout(dropout)
        self.classifier = torch.nn.Linear(768, num_classes)
    def forward(self, input_ids, attention_mask, token_type_ids=None):
        output_1 = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            output_hidden_states=True,
        )
        hidden_state = output_1[0]
        pooler = hidden_state[:, 0]
        pooler = self.pre_classifier(pooler)
        pooler = torch.nn.ReLU()(pooler)
        pooler = self.dropout(pooler)
        output = self.classifier(pooler)
        return output

# ----------------------- Utils -----------------------

def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def resolve_device(hint: str) -> str:
    if hint == 'cpu':
        return 'cpu'
    if hint == 'cuda':
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    return 'cuda' if torch.cuda.is_available() else 'cpu'


@dataclass
class TrainArgs:
    train: str
    weights_out: str
    labels_out: str
    metrics_out: str | None
    model_name: str
    device: str
    max_len: int
    batch_size: int
    epochs: int
    lr: float
    val_batch_size: int
    val_size: float
    seed: int
    grad_accum: int


# ----------------------- Training / Evaluation -----------------------

def make_loaders(df: pd.DataFrame, tok, args: TrainArgs) -> Tuple[DataLoader, DataLoader]:
    train_df, val_df = train_test_split(
        df, test_size=args.val_size, stratify=df['enc_label'], random_state=args.seed
    )
    train_set = Triage(train_df, tok, args.max_len)
    val_set   = Triage(val_df, tok, args.max_len)
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,  num_workers=2, pin_memory=True)
    val_loader   = DataLoader(val_set,   batch_size=args.val_batch_size, shuffle=False, num_workers=2, pin_memory=True)
    return train_loader, val_loader


def evaluate(model, loader, device) -> Tuple[float, float]:
    model.eval()
    total, correct = 0, 0
    total_loss = 0.0
    ce = nn.CrossEntropyLoss()
    with torch.no_grad():
        for batch in loader:
            ids = batch['ids'].to(device)
            mask = batch['mask'].to(device)
            tt  = batch.get('token_type_ids')
            if tt is not None:
                tt = tt.to(device)
            targets = batch['targets'].to(device)
            logits = model(ids, mask, tt)
            loss = ce(logits, targets)
            total_loss += loss.item() * ids.size(0)
            preds = logits.argmax(dim=1)
            correct += (preds == targets).sum().item()
            total += ids.size(0)
    return correct / max(total, 1), total_loss / max(total, 1)


def train_loop(model, loaders, device, args: TrainArgs):
    train_loader, val_loader = loaders

    # Optim/sched (unchanged — keep your current choices)
    optim = torch.optim.Adam(model.parameters(), lr=args.lr)
    scaler = torch.cuda.amp.GradScaler(enabled=(device == 'cuda'))
    ce = nn.CrossEntropyLoss()

    model.to(device)

    last_val_acc, last_val_loss = 0.0, 0.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        for it, batch in enumerate(train_loader, 1):
            ids = batch['ids'].to(device)
            mask = batch['mask'].to(device)
            tt  = batch.get('token_type_ids')
            if tt is not None:
                tt = tt.to(device)
            targets = batch['targets'].to(device)

            with torch.cuda.amp.autocast(enabled=(device == 'cuda')):
                logits = model(ids, mask, tt)
                loss = ce(logits, targets) / max(args.grad_accum, 1)

            scaler.scale(loss).backward()

            if it % args.grad_accum == 0:
                scaler.step(optim)
                scaler.update()
                optim.zero_grad(set_to_none=True)
                

        # End of epoch: evaluate (final metrics reflect the *last* epoch)
        last_val_acc, last_val_loss = evaluate(model, val_loader, device)
        print(f"[epoch {epoch}] val_acc={last_val_acc:.4f} val_loss={last_val_loss:.4f}")

    # Return final-epoch metrics only
    return last_val_acc, last_val_loss


# ----------------------- Main -----------------------

def parse_args() -> TrainArgs:
    ap = argparse.ArgumentParser(description="Fine-tune SecBERT on sentence→TTP classification")
    ap.add_argument("--train", required=True, help="Path to dataset.csv with columns [sentence,label_tec]")
    ap.add_argument("--weights-out", required=True, help="Path to write trained state_dict (e.g., trained_secbert.pt)")
    ap.add_argument("--labels-out", required=True, help="Path to write labels.txt (one label per line in training order)")
    ap.add_argument("--metrics-out", default=None, help="Optional path to write metrics/args JSON")

    ap.add_argument("--model-name", default="jackaduma/SecBERT", help="HF backbone to use")
    ap.add_argument("--device", default="auto", choices=["auto","cpu","cuda"], help="Device selection")
    ap.add_argument("--max-len", type=int, default=512, help="Tokenizer max length")
    ap.add_argument("--batch-size", type=int, default=16, help="Train Batch size")
    ap.add_argument("--val-batch-size", type=int, default=32, help="Train Batch size")
    ap.add_argument("--epochs", type=int, default=10, help="Epochs")
    ap.add_argument("--lr", type=float, default=1e-05, help="Learning rate")
    ap.add_argument("--val-size", type=float, default=0.2, help="Validation split fraction (stratified)")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--grad-accum", type=int, default=1, help="Gradient accumulation steps")

    ns = ap.parse_args()
    return TrainArgs(
        train=ns.train,
        weights_out=ns.weights_out,
        labels_out=ns.labels_out,
        metrics_out=ns.metrics_out,
        model_name=ns.model_name,
        device=ns.device,
        max_len=ns.max_len,
        batch_size=ns.batch_size,
        val_batch_size=ns.val_batch_size,
        epochs=ns.epochs,
        lr=ns.lr,
        val_size=ns.val_size,
        seed=ns.seed,
        grad_accum=ns.grad_accum,
    )


def main():
    args = parse_args()
    set_seed(args.seed)
    device = resolve_device(args.device)

    # Load data
    df = pd.read_csv(args.train)
    if not {'sentence','label_tec'}.issubset(df.columns):
        raise SystemExit("Input CSV must have columns: sentence,label_tec")
    df = df[['sentence','label_tec']].dropna().reset_index(drop=True)

    # Label encoding — save in exact training order
    le = LabelEncoder().fit(df['label_tec'])
    df['enc_label'] = le.transform(df['label_tec'])

    labels = list(le.classes_)
    Path(args.labels_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.labels_out).write_text("\n".join(labels), encoding='utf-8')

    # Tokenizer & loaders
    tok = AutoTokenizer.from_pretrained(args.model_name, use_fast=True)
    train_loader, val_loader = make_loaders(df, tok, args)

    # Model
    model = SecBERTClass(args.model_name, num_classes=len(labels))

    # Train
    _, last_val_acc = train_loop(model, (train_loader, val_loader), device, args)

    # Save LAST-epoch weights (ignore "best")
    state_to_save = {k: v.cpu() for k, v in model.state_dict().items()}
    Path(args.weights_out).parent.mkdir(parents=True, exist_ok=True)
    torch.save(state_to_save, args.weights_out)

    # Metrics/args log
    if args.metrics_out:
        payload = {
            'best_val_acc': float(best_val_acc),
            'args': asdict(args),
            'labels': labels,
        }
        Path(args.metrics_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.metrics_out).write_text(json.dumps(payload, indent=2), encoding='utf-8')

    print(f"[OK] Saved weights to {args.weights_out} and labels to {args.labels_out}")


if __name__ == "__main__":
    main()
