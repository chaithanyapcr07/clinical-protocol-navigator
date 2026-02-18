# Clinical Protocol Navigator Diagrams

## 1) End-to-End Architecture

```mermaid
flowchart TD
    subgraph T["External Triggers"]
        A["CMS Regulatory Site"] --> B["New PDF Release"]
        B --> C["Folder Monitor"]
        C --> D["Auto-Sync"]
        D --> E["OpenClaw Agent"]
        E --> F["Trigger Event"]
        F --> G{"Slack / Discord Hook"}
    end

    G --> H["FastAPI Backend"]

    subgraph P["Dual-Core Python Engine"]
        H --> I{"Routing Logic"}
        I -->|Simple Query| J["RAG Module"]
        I -->|Audit / Temporal Task| K["Long Context Module"]
        J --> L["TF-IDF Retrieval Index"]
        K --> M["Context Caching Engine"]
        L --> N["Top-K Chunks (default 8)"]
        M --> O["High-Token Context Stream"]
    end

    N --> Q["Gemini 3 Flash Preview"]
    O --> Q
    Q --> R["Forensic Reasoning"]
    R --> S["Audit Difference Report"]

    S --> U["Clinical Compliance Team"]
    S --> V["Admin Dashboard"]
    U --> W["Protocol Update Actions"]
    W --> X["Patient Care Improvement"]
```

## 2) UI Execution View (Run Mode + Benchmark)

```mermaid
flowchart TD
    A["Upload Clinical Documents"] --> B["Corpus Loaded"]
    B --> C["Enter Cross-Reference Question"]
    C --> D{"Execution Choice"}
    D -->|Run Selected Mode| E["Single Mode Result Panel"]
    D -->|Run Side-by-Side Benchmark| F["RAG vs Long Context Panel"]

    E --> G["Latency + Context Metrics"]
    E --> H["Answer + Citations [doc|page|paragraph]"]

    F --> I["RAG Output"]
    F --> J["Long Context Output"]
    I --> K["Compare Recall / Precision / Latency"]
    J --> K
```

## Notes

- Diagram 1 reflects the architecture used in this repository (OpenClaw-compatible trigger flow + dual execution engine).
- Diagram 2 reflects the current UI workflow and benchmark interaction model.
- The current retrieval backend is TF-IDF/cosine retrieval (not an external vector database service).
