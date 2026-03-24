# Systematic Codebase Analysis - Overview

## Purpose
This knowledge library documents the methodology for systematically decomposing and analyzing a codebase to understand its architecture, dependencies, and design patterns.

---

# Phase 1: Discovery & Inventory

## Step 1.1: Directory Structure Analysis

### What to analyze:
- **Root level organization** - Configuration files, documentation, entry points
- **Package/Module hierarchy** - Logical grouping of related code
- **File extension patterns** - Language separation vs functional organization
- **Hidden files/directories** - `.git`, build artifacts, secrets management

### Key Questions:
1. What is the top-level architectural pattern? (Monorepo, multi-repo, flat structure)
2. How are related components organized?
3. Where is documentation concentrated?
4. Are there obvious entry points or scaffolding files?

---

## Step 1.2: Entry Point Identification

### Languages & Conventions:
| Language | Common Entry Points |
|-----------|---------------------|
| Python    | `__main__.py`, `app.py`, `server.py` |
| Node.js   | `index.js`, `src/index.ts`, `cli.js` |
| Rust      | `main.rs`, `lib.rs` |
| Go        | `main.go`, `cmd/.../main.go` |
| Java      | `Main.java`, `Application.java` |

---

## Step 1.3: Dependency Inventory

### Build & Runtime Dependencies:
- **Package managers**: `package.json`, `requirements.txt`, `Cargo.toml`, `pom.xml`
- **Import analysis**: What does each file import?
- **Export analysis**: What does each module export?

---

# Phase 2: Architectural Mapping

## Step 2.1: Component Classification

### Common Patterns:
```
├── Controllers/APIs          - External interfaces
├── Services                   - Business logic
├── Models/Entities            - Data structures
├── Repositories/Persistence   - Data access layer
├── Utils/Helpers              - Reusable utilities
└── Configurations              - Settings management
```

---

## Step 2.2: Dependency Graph Construction

### Tools:
- **Static analysis**: IDE dependency view, `npm ls`, `pip tree`
- **Graph visualization**: `graphviz`, `dagre-d3`, online tools like [mermaid.live](https://mermaid.live)

---

# Phase 3: Code Quality Assessment

## Step 3.1: Metrics to Calculate

| Metric | Formula/Tool |
|--------|--------------|
| Cyclomatic Complexity | `E - N + 2P` or tools like `complexity-js`, `pycln` |
| Coupling Between Objects (CBO) | Number of cross-module dependencies |
| Cohesion (LoC per File) | Lines of code / functions |
| Code Coverage | Tool: JaCoCo, istanbul, coveralls |

---

# Phase 4: Documentation Review

## Step 4.1: Document Types
- README files
- API documentation (Swagger/OpenAPI)
- Inline comments and docstrings
- Architecture decision records (ADRs)

---

# Files Created
- `codebase_analysis_overview.md` - Master methodology document

---

*Next: Ready to analyze a specific codebase. Please provide the file paths or repository structure to begin detailed analysis.*