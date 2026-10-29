# mstr_reader: reading Strategy `.mstr` dashboard files

Strategy (MicroStrategy) dashboards arrive as `.mstr` files, alongside the Mosaic model export (YAML).
There is no REST access in the project. `.mstr` is an undocumented binary format; this folder holds
what we know about it, a decoder, and the method used to learn it, so the work can continue on files we can't
see from here.

```
mstrfile.py     format layer: container, block tree, primitive values, dump
decode.py       meaning: objects, visualizations, rows/columns, sorts, limits, filters, derived metrics
to_ir.py        -> Dashboard IR (what the Inspector consumes); used by model_export for dashboards/*.mstr
explore.py      discovery CLI (the commands used to find every rule below)
functions.json  Strategy's standard function ids -> names (system objects, identical in every project)
```

```bash
python -m strategy_agent.tools.mstr_reader.decode  FILE.mstr          # readable summary + issues
python -m strategy_agent.tools.mstr_reader.decode  FILE.mstr --json   # full result
python -m strategy_agent.tools.mstr_reader.explore info FILE.mstr     # version, members, block/object types
```

Standard library only. Nothing here calls Strategy. The synthetic scenarios' dashboards are written with the
same encoding (`synthetic/inspector_synth/mstr.py`), so the Inspector's dashboard checks run on `.mstr` input.

---

## 1. Container

A `.mstr` is a zip. The zip **comment** carries the writer version:
`1;MSTRVer:6=110300;ClientStartVer:6=110300;MSTRFileType:1=0;...;AEVersion:2=13` (`key:len=value`).

| member | content |
|---|---|
| `<projectId>/<dashboardId>.delta` | dashboard definition **and** copies of every metadata object it depends on |
| `<projectId>/<datasetId>/<cubeId>.cube` | cube data snapshot (not decoded, not needed) |
| `<projectId>/<datasetId>/<cubeId>_info.cube` | cube header (not decoded) |

Everything below is about the `.delta`.

## 2. Tagged stream (little-endian)

| bytes | meaning |
|---|---|
| `0e 04 <u16 type> 08 <i64 len> 06 <i32 ~len>` | **block header**; `len` content bytes follow. `~len` (bitwise NOT) repeats the length: this double check is what makes blocks reliable anchors |
| `08 <i64 n>` | int64; if `n < 0` it is a **string** of `~n` bytes, UTF-8, NUL included |
| `06 <i32>` / `05 <i16>` / `02 <f64>` | int32 / int16 / double |
| `12 <u8>` | small enum / flag byte |
| `01 08 <i64 len> 08 <~len> text` | a property **value** string |
| `15 08 <i64 len> 08 <~len> name` | a property **name** (e.g. `TokenStream`, `Hidden`, `MetricSortType`) |
| `09 07 <4> 05 <2> 05 <2> <8>` | **GUID**: dword LE, then two words that are stored **swapped**, then 8 bytes as two LE dwords (`mstrfile.guid_at`) |

Block types: mask with `& 0x0FFF`. `3081` and `b081` are the same kind (`x081`), the high nibble is a wrapper
variant. `x08x` blocks usually wrap a single `0000` content block (`decode._inner`).

Flag bytes vary between files: match `12 ?? 12 00 12 <kind>`, never the full literal (`12 40 ...` vs `12 00 ...`).

## 3. Objects

Two places define objects. Together they give names for everything a dashboard uses; **names never need REST**.

| where | shape | holds |
|---|---|---|
| envelope `00ef` > `00ed` | `06 ver`, `guid id`, `06 type`, `guid project`; sibling `00ef` body > `0002` = name | full metadata copies: dataset (3), attributes (12), metrics (4), tables (15), columns (26), forms (21), db tables (53), the dashboard itself (55) |
| embedded `00ae` (inside `00ad` lists) | `guid id`, `06 type`, `0002` child = name (may be empty) | the dashboard's own objects: templates (2), filters (1), derived metrics (4), aggregate metrics (7), layout objects (61) |

Object type codes match Strategy's object types: 1 filter, 2 template, 3 report/cube, 4 metric,
7 aggregate metric, 11 function, 12 attribute, 13 fact, 15 table, 21 attribute form, 26 column, 53 db table,
55 dashboard. Unnamed embedded metrics are helpers (e.g. `Rank(x)` behind a top-N, `Sum(fact)` behind a dataset
metric); `render` expands them in place.

Display renames: a unit's own alias (value string right after its `TokenStream` property) or a dashboard-level
rename (`0000` block `12 00 06 <n> 06 <locale> 01 08 <name> .. guid 12 <type>`, `decode.dataset_aliases`).
Always keep both: the alias is what users see, the object id is what the data means.

## 4. Dashboard structure (rules in `decode.py`)

**Visualizations.** In the layout tree: a `0000` block whose parent is `x085`, content starts `12 02`, and whose
*own* strings (not inside child blocks) are `name, [subtitle..], source label, key`. The key looks like `W123`, `K45`,
`W<32 hex>` or `IGK<32 hex>`. The **template guid and dataset guid** follow the end of an enclosing block
directly; fallback: the first type-2 object guid inside the innermost enclosing block (grids with column sets).

**Template axes.** Blocks whose content starts `12 3c`. In template order: **first = rows, second = columns**
(the two following ints are counts, not the axis id).

**Units.** Inside an axis: blocks with parent `x083` and content `12 xx 12 <kind>` where kind `00` attribute,
`04` the "Metrics" placeholder, `08` metric; or `12 xx` directly followed by a block (= attribute). The unit's
object id is the first non-null guid between this unit and the next one (attribute ids sit just *after* the
`b083`, metric ids *inside* it).

**Sorts.** `x084` blocks in the template whose first child starts `12 ?? 12 00 12 <kind>`; the sorted object's
guid is inside; `kind & 0x08` = ascending (`0x2c`), else descending (`0x24`).

**Metric limits (top-N) vs thresholds.** Both are comparison operators (`<=`, `>`, ...) in the template.
Inside a `12 10` container = **threshold** (conditional formatting, no effect on numbers). Directly under a
metric unit = **limit**, e.g. `Rank([Revenue]) <= 10` (Top 10) or `Rank(x) > 1` (Exclude Top 1).
`In(...)` on attributes found there are element conditions, not limits.

**View filters.** Type-1 objects referenced from the template guid list; expression as below
(`And(In([Year], {2021}))`, `Not In`, `IsNotNull`, dates).

**Dashboard filters (selectors).** `x089` blocks. Own strings: selected element names (each written twice),
then the selector name, then its key (`W12`, `IGK34`, `W<32 hex>`); first guid = source attribute/metric; function guids inside =
qualification (e.g. Between on dates). `(All)` means everything selected.

**Expressions** (derived metrics, filters, limits). Root: object `0006` > `x08x` > `0000` > first `x081`,
content `i32 node type` + block. Node types:

| type | node | encoding |
|---|---|---|
| 4 | operator | child list `x081` (`i32 count`, then `i32 type` + block per child), then the **function guid** (`functions.json`) |
| 5 | object reference | guid + `12 <object type>` |
| 3 | constant | `12 ?? 06 <i32> 05 <VARTYPE>` then value: `06` int, `02` double, `05` int16, `08`/`01 08 .. 08` string |
| 2 | element list | strings `h<value>;<attribute guid>` |
| 6 | date-time constant | `0a` then i32 year, month, day, hour, minute, second |
| 1 | attribute (form) reference | guids: attribute, forms |

Functions not in `functions.json` show as a 32-hex id: either a newer standard function or a project-specific
one. The decoder reports them as issues.

## 5. Into the IR (`to_ir.py`)

| .mstr | IR |
|---|---|
| attribute units (all axes, incl. drop zones) | `QuerySpec.dimensions` |
| metric units | `QuerySpec.measures` (a Rank helper unit reports the ranked metric) |
| sorts | `QuerySpec.sort` |
| limit `Rank(x) <= N` | `QuerySpec.top_n = N`; other limits as text in `QuerySpec.unparsed` |
| view filter `In`/`Not In`/comparison against constants | `QuerySpec.filters`; anything else as text in `unparsed` |
| selector with selected elements / a qualification | `Dashboard.filters` (`in` + values / the function, values not decoded) |
| derived metrics | `Dashboard.fields` (`Field`, `object_type = mstr:derived_metric`) |
| datasets | `Dashboard.models` (model name when the dataset id is a model's object id) |

Field names come from the **model export** first: a unit's object id is matched to `Field.source.object_id`.
Only when that fails does the .mstr's own name (or alias) appear, with a warning
"object X (id) is not in the model export". Those warnings are the first thing to check in the project: they test
the `.mstr` <-> model export id join.

## 6. How well it works (evidence)

Checked against Strategy's REST answer for the same dashboards: **81 public demo dashboards, 1,540
visualizations** (the full public demo library except one with download disabled), all written by **one**
Strategy version (MSTRVer 110300, AEVersion 13). Dashboards sit on cubes and Excel datasets, **not Mosaic models**.

| area | result |
|---|---|
| dataset object names from the `.mstr` alone | 4854 / 4856 |
| derived metric expressions fully decoded | 1335 / 1335 |
| visualizations linked to their template | 1517 / 1540 (98.5 %) |
| every shown attribute/metric found in the template | 95.3 % |
| exact rows/columns split | 83.3 % (see section 7) |
| displayed names (alias or object name) | 98.3 % |
| sorts: found / direction | 158 / 162, 158 / 158 |
| metric limits / top-N (incl. relative-date and value limits) | 123 / 125 |
| view filters | 283 / 297 |
| dashboard filters: found, source, selected elements, qualification | 195 / 195, 195 / 195, 60 / 60, 10 / 10 |

## 7. Known gaps

- **Extra template units** (~20 % of visualizations): templates hold drop-zone units (colour-by, tooltip, break-by)
  that the grid view does not list. They are part of the query grain; how a unit is assigned to a drop zone is
  not decoded yet.
- **Report-builder grids**: templates with every candidate attribute; the shown subset comes from runtime selection.
- Rank helper metrics used by a limit can appear in place of the ranked metric.
- References to objects that are not copied into the file (project schema objects) stay as 8-hex ids.
- **Count distinct**: `Count` operators look identical with and without `Distinct=True` in the samples
  (the parameter block is the same). Every Count gets a warning; confirm in Strategy. This can change numbers.
- **Chart type** is not decoded (IR `chart_type = other`; cosmetic).
- **Selector scope** (which visualizations a filter targets) is not decoded; IR treats filters as dashboard-wide.
- **Custom groups / derived elements** in filters: the element list comes back empty (seen in "Age Groups"
  filters); the filter is then reported with no values.
- Qualification selector values (e.g. Between bounds) are not decoded; only the function.
- Layout items without a template are text/image/panel widgets (904 in the demo set; none of the 453 checked
  visualizations was among them).
- Not seen in the demo files: prompts, custom groups, consolidations, derived elements, security filters,
  **Mosaic-model datasets**. Expect new object types / node types there.
- **Mosaic link untested**: the plan is to join `.mstr` dataset object ids to `information.objectId` in the
  model export. Verify that first (section 8, step 6).

## 8. Discovery playbook (how every rule above was found)

Work from something you *know* to the bytes, never the other way round. Each step uses `explore.py`.

1. **Survey.** `explore info FILE` - version, block and object types. On a new version run
   `explore compare KNOWN.mstr NEW.mstr` to list block types, property names and object types that are new.
2. **Anchor on a known value.** Take a name you can see in the dashboard (a viz title, a metric, a filter value):
   `explore where FILE "Revenue"`. It prints every hit with its block chain (`type:first-bytes@offset`) and the
   strings and guids around it. GUIDs work the same: `explore where FILE <32 hex>`.
3. **Read the enclosing structure.** `explore object FILE <guid>` dumps an object with function and object names
   resolved; `explore dump FILE <offset>` dumps any block. Look for: which block types repeat per item, which
   first bytes (`12 xx`) distinguish kinds, where the guid sits (inside the block or right after it).
4. **Generalise by shape, not offset.** Write rules as "block of type `x0yy` (masked) whose content starts with
   `..` and whose parent is `x0zz`". Treat `12 ??` second bytes as flags. Prefer the block's *own* strings over
   all nested strings. Use the length double-check for anything you parse sequentially.
5. **Find flags with a minimal pair.** Export the same dashboard twice with one change (sort ascending vs
   descending, Top 5 vs Top 10, filter on vs off) and diff the two dumps of the same object. This is how the
   ascending bit (`0x08`) and the limit constant were confirmed.
6. **Confirm with what you have.** No REST in the project, so confirm each rule against: (a) the dashboard as
   seen in Strategy (editor rows/columns, filter panel, metric formula editor), (b) the model export
   (ids and names must match `information.objectId` / names), (c) many files at once - a rule that holds on one
   file and fails on the next is a coincidence.
7. **Record it.** Add the rule to section 4 with the evidence, and to `decode.py`. Anything you cannot decode
   must surface in `issues` (the Inspector turns it into UNVERIFIED, never PASS).

Dead ends already tried: string extraction (`mstr-unpacker` style) gives names but loses which visualization uses
what; IDs are never stored as hex text (except layout keys); offsets are not stable between files.
