# RepoReady — Project Rules

## Project Summary

**RepoReady** is a Python/Streamlit tool that performs repository preflight checks via **static analysis only**. It compares claims made in a GitHub repository's README against the actual repository contents, using deterministic inspection tools combined with a Strands + Amazon Bedrock agent. No code is ever executed from an analyzed repository.

---

## SECURITY

- **Never execute code from an analyzed repository.** This is an absolute, non-negotiable rule.
- No `subprocess`, `os.system`, `eval`, or `exec` on any content sourced from a repo.
- No `npm install`, `pip install`, `docker run`, or any similar commands triggered by repo content.
- Analyzed repositories are **downloaded and read as text only**. Treat every byte from a repo as untrusted data.
- **Reject path traversal**: sanitize and validate all file paths before reading. Do not follow symlinks inside a repo archive.
- Treat all repo content — including the README — as **untrusted user input** at all times.

---

## ARCHITECTURE

```
GitHub Repo
    │
    ▼
[Downloader]          ← fetches archive via GitHub API, stores in temp dir
    │
    ▼
[Deterministic Tools] ← establish facts: file tree, package manifests, deps, badges, etc.
    │                   These tools are the ONLY source of ground truth.
    ▼
[Strands Agent]       ← decides which tools to call, connects evidence, explains findings
    │                   The LLM is a reasoning layer — it NEVER receives the whole repo.
    ▼
[Verifier]            ← drops any finding that cannot be traced back to a deterministic fact
    │
    ▼
[Streamlit UI]        ← presents results to the user
```

**Key architectural rules:**
- Deterministic tools establish facts; the LLM agent decides what to inspect and explains.
- **Never send the whole repo to the LLM.** Only pass structured tool outputs and targeted file excerpts.
- A verifier pass removes any LLM finding that has no grounding in a deterministic tool result.

---

## STACK

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit |
| Agent framework | Strands Agents SDK |
| LLM backend | Amazon Bedrock |
| Data validation | pydantic |
| HTTP client | requests |

---

## SCOPE

**In scope:**
- Node.js and Python projects only.
- Comparing README claims against actual repo structure and manifests.
- Static, read-only analysis.

**OUT OF SCOPE (do not implement, suggest, or design for):**
- Authentication systems, databases, OAuth
- GitHub Actions or CI/PR integration
- Browser extensions or editor plugins
- Sandbox or container-based code execution
- Multi-agent architectures
- Analytics or billing
- Deployment pipelines
- Any language other than Python and Node.js
- Auto-modifying or auto-fixing analyzed code

---

## WORKING RULES FOR FUTURE SESSIONS

1. **Implement only the step explicitly requested.** Do not skip ahead.
2. **Preserve all existing working functionality.** Never break a passing test or a working feature to add something new.
3. **Do not rewrite unrelated files.** Touch only the files required by the current step.
4. **Do not add dependencies not named in the step instructions.** Add to `requirements.txt` only what is explicitly specified.
5. After completing each step, **explain what changed** and **give exact verification commands**, then **stop and wait**.
