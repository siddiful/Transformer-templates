# -*- coding: utf-8 -*-
"""Seq2Seq.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/12ZLjDIxOIj6wkXwGkQwPdf3F3bDsA7d_
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import dataset
import numpy as np
import matplotlib.pyplot as plt

class MultiHeadAttention(nn.Module):
  def __init__(self, d_model, d_k, n_heads, max_len, causal):
    super().__init__()
    self.d_k = d_k
    self.n_heads = n_heads
    self.query = nn.Linear(d_model, d_k*n_heads)
    self.key = nn.Linear(d_model, d_k*n_heads)
    self.value = nn.Linear(d_model, d_k*n_heads)
    self.fc = nn.Linear(d_k*n_heads, d_model)
    self.causal = causal
    if causal:
      cm = torch.tril(torch.ones(max_len, max_len))
      self.register_buffer('causal_mask', cm.view(1, 1, max_len, max_len))

  def forward(self, q, k, v, pad_mask=None):
    q = self.query(q) # (B, T, d_k*n_heads)
    k = self.query(k) # (B, T, d_k*n_heads)
    v = self.query(v) # (B, T, d_k*n_heads)

    B = q.shape[0]
    T_output = q.shape[1]
    T_input = v.shape[1]

    q = q.view(B, T_output, self.n_heads, self.d_k).transpose(1, 2) # (B, n_heads, T_output, d_k)
    k = k.view(B, T_input, self.n_heads, self.d_k).transpose(1, 2) # (B, n_heads, T_input, d_k)
    v = v.view(B, T_input, self.n_heads, self.d_k).transpose(1, 2) # (B, n_heads, T_input, d_k)

    dot_prod = q @ k.transpose(-2, -1) / math.sqrt(self.d_k) # (B, n_heads, T_output, T_input)
    if pad_mask is not None:
      dot_prod = dot_prod.masked_fill(pad_mask[:, None, None, :]==0, float('-inf'))
    if self.causal:
      dot_prod = dot_prod.masked_fill(self.causal_mask[:, :, :T_output, :T_input]==0, float('-inf'))
    attention_wts = F.softmax(dot_prod, dim=-1) # (B, n_heads, T_output, T_input)
    A = attention_wts @ v # (B, n_heads, T_output, d_k)
    A = A.transpose(1, 2)
    A = A.contiguous().view(B, T_output, self.d_k * self.n_heads) # (B, T_output, n_heads*d_k)

    return self.fc(A)

class EncoderBlock(nn.Module):
  def __init__(self, d_model, d_k, n_heads, max_len, dropout_prob = 0.1):
    super().__init__()
    self.mha = MultiHeadAttention(d_model, d_k, n_heads, max_len, causal=False)
    self.ln1 = nn.LayerNorm(d_model)
    self.ln2 = nn.LayerNorm(d_model)
    self.ffn = nn.Sequential(
        nn.Linear(d_model, 4*d_model),
        nn.GELU(),
        nn.Linear(4*d_model, d_model),
        nn.Dropout(dropout_prob),
    )
    self.dropout = nn.Dropout(p=dropout_prob)

  def forward(self, x, pad_mask=None):
    x = self.ln1(x + self.mha(x, x, x, pad_mask))
    x = self.ln2(x + self.ffn(x))
    x = self.dropout(x)
    return x

class DecoderBlock(nn.Module):
  def __init__(self, d_model, d_k, n_heads, max_len, dropout_prob = 0.1):
    super().__init__()
    self.mha1 = MultiHeadAttention(d_model, d_k, n_heads, max_len, causal=True)
    self.mha2 = MultiHeadAttention(d_model, d_k, n_heads, max_len, causal=False)
    self.ln1 = nn.LayerNorm(d_model)
    self.ln2 = nn.LayerNorm(d_model)
    self.ln3 = nn.LayerNorm(d_model)
    self.ffn = nn.Sequential(
        nn.Linear(d_model, 4*d_model),
        nn.GELU(),
        nn.Linear(4*d_model, d_model),
        nn.Dropout(dropout_prob),
    )
    self.dropout = nn.Dropout(p=dropout_prob)

  def forward(self, enc_output, dec_input, enc_mask=None, dec_mask=None):
    x = self.ln1(dec_input + self.mha1(dec_input, dec_input, dec_input, dec_mask))
    x = self.ln2(x + self.mha2(x, enc_output, enc_output, enc_mask))
    x = self.ln3(x + self.ffn(x))
    x = self.dropout(x)
    return x

class PositionalEncoding(nn.Module):
  def __init__(self, d_model, max_len=2048, dropout_prob=0.1):
    super().__init__()
    self.dropout = nn.Dropout(p=dropout_prob)

    position = torch.arange(max_len).unsqueeze(1)
    exp_term = torch.arange(0, d_model, 2)
    div_term = torch.exp(exp_term * (-math.log(10000.0) / d_model))
    pe = torch.zeros(1, max_len, d_model)
    pe[0, :, 0::2] = torch.sin(position * div_term)
    pe[0, :, 1::2] = torch.cos(position * div_term)
    self.register_buffer('pe', pe)

  def forward(self, x):
    x = x + self.pe[:, :x.size(1), :]
    return self.dropout(x)

class Encoder(nn.Module):
  def __init__(self, d_model, d_k, n_heads, max_len, vocab_size , n_layers, dropout_prob):
    super().__init__()
    self.embedding = nn.Embedding(vocab_size, d_model)
    self.pos_encoding = PositionalEncoding(d_model, max_len, dropout_prob)
    self.transformer_blocks = nn.Sequential(*[EncoderBlock(d_model, d_k, n_heads, max_len, dropout_prob) for _ in range(n_layers)])
    self.ln = nn.LayerNorm(d_model)

  def forward(self, x, pad_mask=None):
    x = self.embedding(x) #(B, T, d_model)
    x = self.pos_encoding(x) #(B, T, d_model)

    for block in self.transformer_blocks:
      x = block(x, pad_mask)

    x = self.ln(x) #(B, d_model)

    return x

class Decoder(nn.Module):
  def __init__(self, d_model, d_k, n_heads, max_len, vocab_size , n_layers, dropout_prob):
    super().__init__()
    self.embedding = nn.Embedding(vocab_size, d_model)
    self.pos_encoding = PositionalEncoding(d_model, max_len, dropout_prob)
    self.transformer_blocks = nn.Sequential(*[DecoderBlock(d_model, d_k, n_heads, max_len, dropout_prob) for _ in range(n_layers)])
    self.ln = nn.LayerNorm(d_model)
    self.fc = nn.Linear(d_model, vocab_size)

  def forward(self, enc_output, dec_input, enc_mask=None, dec_mask=None):
    x = self.embedding(dec_input) #(B, T_output, d_model)
    x = self.pos_encoding(x) #(B, T_output, d_model)

    for block in self.transformer_blocks:
      x = block(enc_output, x, enc_mask, dec_mask)

    x = self.ln(x) #(B, d_model)
    x = self.fc(x) #(B, vocab_size)

    return x

class Transformer(nn.Module):
  def __init__(self, encoder, decoder):
    super().__init__()
    self.encoder = encoder
    self.decoder = decoder

  def forward(self, enc_input, dec_input, enc_mask=None, dec_mask=None):
    enc_output = self.encoder(enc_input, enc_mask)
    dec_output = self.decoder(enc_output, dec_input, enc_mask, dec_mask)
    return dec_output

encoder = Encoder(64, 16, 4, 512, 20000, 4, 0.1)
decoder = Decoder(64, 16, 4, 512, 10000, 4, 0.1)
transformer = Transformer(encoder, decoder)

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
encoder = encoder.to(device)
decoder = decoder.to(device)
transformer = transformer.to(device)

transformer

xe = np.random.randint(0, 20000, (8, 512))
xe_t = torch.tensor(xe).to(device)

maske = np.ones((8, 512))
maske[:, 256:]=0
maske_t = torch.tensor(maske).to(device)

xd = np.random.randint(0, 10000, (8, 256))
xd_t = torch.tensor(xd).to(device)

maskd = np.ones((8, 256))
maskd[:, 128:]=0
maskd_t = torch.tensor(maskd).to(device)

out = transformer(xe_t, xd_t, maske_t, maskd_t)
out.shape

out

!wget -nc https://lazyprogrammer.me/course_files/nlp3/spa.txt

!head spa.txt

import pandas as pd
df = pd.read_csv('spa.txt', sep='\t', header=None)
df.head()

df = df.iloc[:30000]

df.columns = ['en', 'es']
df.to_csv('spa.csv', index=None)

!head spa.csv

!pip install transformers datasets sentencepiece sacremoses

from datasets import load_dataset
raw_dataset = load_dataset('csv', data_files="spa.csv")

raw_dataset

split = raw_dataset['train'].train_test_split(test_size=0.3, seed=42)
split

from transformers import AutoTokenizer

checkpoint = "Helsinki-NLP/opus-mt-en-es"
tokenizer = AutoTokenizer.from_pretrained(checkpoint)

en_sentence = split['train']['en'][0]
es_sentence = split['train']['es'][0]

inputs = tokenizer(en_sentence)
targets = tokenizer(text_target=es_sentence)

tokenizer.convert_ids_to_tokens(targets['input_ids'])

max_input_length = 128
max_target_length = 128

def preprocess_fn(batch):
  tokenized_inputs = tokenizer(batch['en'], truncation=True, max_length=max_input_length)
  targets = tokenizer(text_target=batch['es'], truncation=True, max_length=max_target_length)
  tokenized_inputs['labels'] = targets['input_ids']
  return tokenized_inputs

tokenized_datasets = split.map(preprocess_fn, batched=True, remove_columns=split['train'].column_names)

tokenized_datasets

from transformers import DataCollatorForSeq2Seq

data_collator = DataCollatorForSeq2Seq(tokenizer)

batch = data_collator([tokenized_datasets['train'][i] for i in range(0, 5)])

batch

print(tokenizer.all_special_ids)
print(tokenizer.all_special_tokens)
print(tokenizer.vocab_size)

from torch.utils.data import DataLoader

train_dataloader = DataLoader(
    tokenized_datasets['train'],
    shuffle=True,
    batch_size=32,
    collate_fn=data_collator,
)

valid_dataloader = DataLoader(
    tokenized_datasets['test'],
    batch_size=32,
    collate_fn=data_collator,
)

tokenizer.add_special_tokens({'cls_token':'<s>'})

tokenizer('<s>')

tokenizer.vocab_size

encoder = Encoder(64, 16, 4, 512, tokenizer.vocab_size+1, 4, 0.1)
decoder = Decoder(64, 16, 4, 512, tokenizer.vocab_size+1, 4, 0.1)
transformer = Transformer(encoder, decoder)

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
encoder = encoder.to(device)
decoder = decoder.to(device)
transformer = transformer.to(device)

loss_fn = nn.CrossEntropyLoss(ignore_index=-100)
optimizer = torch.optim.AdamW(transformer.parameters())

from datetime import datetime

def train(train_dataloader, valid_dataloader, model, loss_fn, optimizer, epochs):
  train_losses = np.zeros(epochs)
  val_losses = np.zeros(epochs)

  for epoch in range(epochs):
    train_loss = []
    t0 = datetime.now()
    model.train()
    for batch in train_dataloader:
      batch = {k:v.to(device) for k, v in batch.items()}
      optimizer.zero_grad()
      enc_input = batch['input_ids']
      enc_mask = batch['attention_mask']
      targets = batch['labels']

      dec_input = targets.clone().detach()
      dec_input = torch.roll(dec_input, shifts=1, dims=1)
      dec_input[:, 0] = 65001
      dec_input = dec_input.masked_fill(dec_input==-100, tokenizer.pad_token_id)
      dec_mask = torch.ones_like(dec_input)
      dec_mask = dec_mask.masked_fill(dec_input==tokenizer.pad_token_id, 0)

      output = model(enc_input, dec_input, enc_mask, dec_mask)
      loss = loss_fn(output.transpose(1, 2), targets)
      loss.backward()
      optimizer.step()
      train_loss.append(loss.item())

    train_losses[epoch] = np.mean(train_loss)
    val_loss = []
    model.eval()
    for batch in valid_dataloader:
      batch = {k:v.to(device) for k, v in batch.items()}
      enc_input = batch['input_ids']
      enc_mask = batch['attention_mask']
      targets = batch['labels']

      dec_input = targets.clone().detach()
      dec_input = torch.roll(dec_input, shifts=1, dims=1)
      dec_input[:, 0] = 65001
      dec_input = dec_input.masked_fill(dec_input==-100, tokenizer.pad_token_id)
      dec_mask = torch.ones_like(dec_input)
      dec_mask = dec_mask.masked_fill(dec_input==tokenizer.pad_token_id, 0)

      output = model(enc_input, dec_input, enc_mask, dec_mask)
      loss = loss_fn(output.transpose(1, 2), targets)
      val_loss.append(loss.item())

    val_losses[epoch] = np.mean(val_loss)
    dt = datetime.now() - t0
    print(f'Epoch {epoch} Train loss: {train_losses[epoch]}, Val loss: {val_losses[epoch]}')
  return train_losses, val_losses

train_losses, val_losses = train(train_dataloader, valid_dataloader, transformer, loss_fn, optimizer, 15)

sample_sentence = split['test'][10]['en']
sample_sentence

enc_input = tokenizer(sample_sentence, return_tensors='pt').to(device)
enc_input

gen_str = '<s>'
dec_input = tokenizer(text_target=gen_str, return_tensors='pt').to(device)
dec_input

output = transformer(enc_input['input_ids'], dec_input['input_ids'][:, :-1], enc_input['attention_mask'], dec_input['attention_mask'][:, :-1])
output