# Synthetic demo documents

Every file in this directory is fabricated for demonstration and testing. Names, addresses,
identifiers, account numbers, and transactions do not describe real people or organizations.

Run the generator from the repository root:

```bash
uv run --project backend python examples/generate_demo_documents.py
```

The generated images contain a visible `SYNTHETIC DEMO` banner and may be safely used in product
screenshots. Do not add customer documents, production exports, or screenshots containing real
account data to this repository.
