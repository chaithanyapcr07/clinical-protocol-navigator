# UI Benchmark Layout (from Live Run Screen)

This diagram maps the exact interface flow shown in the live app screenshot:

```mermaid
flowchart TD
    A["Clinical Protocol Navigator Header"] --> B["1) Upload Clinical Documents"]
    B --> C["Choose Files"]
    C --> D["Upload"]
    D --> E["Loaded Documents List"]

    E --> F["2) Ask a Cross-Reference Question"]
    F --> G["Question Textarea"]
    G --> H["Mode Selector (RAG / Long Context)"]

    H --> I["Run Selected Mode"]
    H --> J["Run Side-by-Side Benchmark"]

    I --> K["Left Result Panel"]
    K --> L["Mode + Latency + Context"]
    L --> M["Answer + Citations"]

    J --> N["Right Result Panel"]
    N --> O["RAG Section"]
    N --> P["LONG CONTEXT Section"]
    O --> Q["RAG Metrics + Answer + Citations"]
    P --> R["Long Context Metrics + Answer + Citations"]
```

## Interpretation

- Left panel represents the selected single-mode run output.
- Right panel represents combined benchmark output (`RAG` and `LONG CONTEXT`).
- Both views expose latency and context-size metrics, then answer and citation blocks.
