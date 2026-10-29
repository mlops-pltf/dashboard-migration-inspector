# Inspector: notes for coding agents

- Strategy `.mstr` dashboard files are an undocumented binary format. Before touching anything that reads them,
  read `packages/strategy_agent/strategy_agent/tools/mstr_reader/README.md`: format, decoding rules, evidence,
  known gaps and the discovery playbook (use `explore.py`, confirm every new rule on several files, record it).
- In the project, Strategy input is only the `.mstr` plus the model export (YAML); ThoughtSpot input is only `.tml`.
  No REST calls.
- Anything the decoder cannot read must be reported as an issue, never guessed.
