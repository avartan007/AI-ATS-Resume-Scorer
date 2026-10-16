# models/

Put your locally-trained model(s) in this folder.

Example layout:

```
models/
└── bert-ats/                 <- your trained model folder
    ├── config.json
    ├── model.safetensors     (or pytorch_model.bin)
    ├── tokenizer.json
    ├── vocab.txt
    ├── modules.json                    # required by sentence-transformers
    ├── config_sentence_transformers.json
    └── 1_Pooling/                      # required by sentence-transformers
```

Then point the backend at it by adding this line to the **root** `.env`
(`ats-scorer/.env`, not `backend/.env`):

```
SENTENCE_TRANSFORMER_MODEL=/absolute/path/to/models/bert-ats
```

The backend loads this at startup in `backend/main.py`:

```python
app.state.embedder = SentenceTransformer(SENTENCE_TRANSFORMER_MODEL)
```

If `SENTENCE_TRANSFORMER_MODEL` is not set it falls back to `all-MiniLM-L6-v2`.
`all-MiniLM-L6-v2` and similar sentence-transformers models are the kind this
folder expects (a **SentenceTransformer**, not a plain HuggingFace BERT).
