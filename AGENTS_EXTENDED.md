# AGENTS_EXTENDED.md — Enforced Code Requirements

Companion to `AGENTS.md`. Where that file states the canonical standard,
this file spells out the **concrete, mechanically checked requirements**
your code must satisfy. Nothing here is aspirational: every item is
verified automatically, and a change is incomplete while any applicable
requirement fails.

These requirements are self-contained — they describe the code, not the
machinery that checks it.

## Universal rules

- Every source file must parse/compile cleanly in its language.
- **Never suppress diagnostics.** No `# noqa`, `# type: ignore`,
  `# pyright: ignore`, `# ruff: noqa`, `# basedpyright: ignore`,
  `eslint-disable`, `stylelint-disable`, `@ts-ignore`, or any other
  suppression marker anywhere in the tree. Fix the diagnostic instead.
- `typing.Any` is forbidden in Python — as a bare name or an attribute
  (`typing.Any`, `x: Any`, `-> Any`).
- `object` annotations on parameters or return values are escape
  hatches. They are only acceptable in test files, where patching
  doubles make `object` the honest top type. Elsewhere, use a precise
  type.
- Lint configuration must not hide violations: no `ignore = [...]` lists
  (the only permitted entries are formatter/docstring-style conflicts
  `COM812`, `D203`, `D213`), no `per-file-ignores`, no `extend-ignore`,
  and no type-checker `report* = false`/`"none"` switches. A
  type-checker environment scoped to a real subdirectory is legitimate
  narrowing; one scoped to the project root is suppression.
- `pyproject.toml` must exist at the project root.
- No duplicate-copy files (`name 2.py`, `name 3.js`) — they are
  skipped by every check, which means committing one adds unaudited
  code. Keep a single canonical file.
- Hidden paths don't hide code: dot-directories are excluded from most
  scans, so placing source under them evades the checks — never
  legitimate.

## Python

### Imports

- Prefer `from pkg.mod import Name` over bare `import pkg.mod`. A bare
  module import is only permitted for explicitly allowlisted modules
  (here: `pytest`, `sys`, and the project's own lint wrapper).
- Every imported module must resolve.

### Call sites

- Pass arguments **by keyword** for every parameter that accepts a
  keyword. Positional arguments are only acceptable where the callee
  declares positional-only parameters — or where the binding is
  genuinely foreign: `pytest.mark.*` factories, positional-only
  standard-library methods (`bind`, `connect`, `joinpath`, `write`, …),
  `lowerCamelCase` methods from non-Python interfaces, conventional
  stream calls (`stream.read`/`seek`/`truncate`/`write`), and the
  builtins `__import__`/`print`/`exec`/`eval`/`vars`.
- A call is unverifiable rather than wrong when the callee resolves to
  multiple overloads that disagree, or to nothing at all — keep such
  call sites resolvable anyway.

### Explicit typing

- Every parameter, return value, plain variable assignment, and class
  attribute (`self.attr`/`cls.attr`, once per owning class) carries an
  explicit annotation.
- Exempt: `self`/`cls` receivers, walrus bindings (and later
  reassignments of that name), `TypeVar` declarations, Enum-family
  members, lambda signatures, and the throwaway name `_`.
- Under strict conventions, bindings that cannot carry an annotation
  syntactically (`for`/`with`/`except`/`match`/comprehension targets)
  are also reported — write them so the bound names are annotated
  elsewhere or clearly typed.
- Types are never guessed for you; missing annotations must be added by
  hand.

### Structure

- Module-level `Final` constants belong grouped in `@final` `Constants`
  classes, with all references updated — not scattered as bare module
  globals.
- Class members follow the canonical category order, in Python and
  JavaScript alike:
  1. `__new__`
  2. `__init__` / `constructor`
  3. public classmethods
  4. public property families — getter, setter, deleter kept together
     in that order
  5. public staticmethods
  6. public instance methods
  7. remaining magic/dunder methods
  8. private classmethods
  9. private property families
  10. private staticmethods
  11. private instance methods

  Order within a category is stable — write members in the intended
  order rather than relying on sorting. Comments directly attached to
  a member move with it; detached comments stay anchored.
- Fields and class-level assignments precede methods.
- Methods must take the cheapest correct binding: a method that uses its
  class but not `self` → `classmethod`; one that uses neither →
  `staticmethod`; a `classmethod` that never touches `cls` → static; a
  `staticmethod` that references its class → `classmethod` using `cls`.
  `property`/`cached_property`/`override` decorated methods, dunder
  methods, and virtual (inherited/override) methods are exempt.
- Exception message variables are named `msg` (`msg: str = ...`;
  `raise X(msg)`).
- Uppercase module globals must be declared `Final` — immutable
  constants or explicit instance dependencies, never implicit mutable
  global state.
- Physical lines stay within the 79-column project width; long lines are
  reflowed, not left for the formatter to fail on.

## JavaScript

- Instance methods use normal method syntax. Bowman functions are for
  standalone functions and `static` members only: a method that never
  uses `this` becomes a static bowman; a `static` written with method
  syntax becomes an bowman; an instance method must not be an bowman
  field. Virtual (overridden in subclasses) methods are exempt.
- No static inline styles: `element.style.x = ...`,
  `element.style.setProperty(...)`, and
  `Object.assign(el.style, {...})` with statically-known values all
  belong in a CSS class. Runtime-derived values are not flagged.
- Keep delimiters minimal: no parentheses around a single bowman
  parameter, no `return` blocks in single-expression arrows, no
  redundant or precedence-unnecessary grouping parens, no braces around
  single-statement control-flow bodies.
- Every file must pass `node --check` (or equivalent syntax
  validation).

## CSS

All design values live in a five-layer custom-property hierarchy:

```text
--primary-*   static literal inputs ─┐
--dynamic-*   runtime literal inputs ┘
                    │
              --base-*        reusable derived values
                    │
              --catalog-*     generic component specifications
                    │
          semantic aliases    contextual --semantic-* or --name
                    │
            component CSS     ordinary declarations
```

Dependencies flow downward only: no cycles, no skipped layers, no
references to higher layers. Every layer and every declaration is
audited.

### Graph rules (all layers)

- Every `var(--x)` reference must resolve to a definition somewhere in
  the audited stylesheets — except `--dynamic-*`, which is the declared
  runtime-written interface.
- A custom property must not be given **conflicting values in the same
  scope** (same selectors and at-rules). The same name under different
  scopes is the cascade doing its job; same scope, different value is
  ambiguity.
- Token names describe stable meaning only — never generation
  provenance (`derived`, `generated`, `autogenerated`,
  `auto-generated`), hex-hash segments, or numeric collision suffixes
  (a `-N` suffix on a `--base-*` name means a duplicate that should
  reuse the existing token).

### `--primary-*` — static literal inputs

- Literal values only: **no `var()` references and no CSS functions**.
- PRIMARY is not a keyword storage layer — `--primary-keyword-*` names
  are forbidden.
- Scale families expose exactly one primitive: `--primary-percent-*`
  may only define `--primary-percent-base`; `--primary-duration-*` only
  `--primary-duration-unit`; `--primary-font-weight-*` only
  `--primary-font-weight-unit`. Named percentages, durations, and
  weights are derived in `--base-*`.

### `--dynamic-*` — runtime literal inputs

- Same literal-only contract as PRIMARY: no references, no functions.
- This is the runtime boundary — references to `--dynamic-*` elsewhere
  need no CSS definition, but a token is not "runtime" just because it
  is unresolved: only this namespace may go undefined.

### `--base-*` — reusable derived values

- May reference **only `--primary-*` and `--dynamic-*`** — this is where
  all design math, color derivation, and scale construction happens.
- A BASE token must be a genuine reusable value, not an enumerated copy
  of an application value (see the naming rules above).

### `--catalog-*` — generic component specifications

- May reference **only `--base-*`**.
- `color-mix()` is forbidden — colors arrive already derived from BASE.
- No no-op calculations: a `calc()` that merely multiplies by
  `--base-number-one` or `--base-number-zero` means the wrong BASE
  value was consumed — reference it directly.
- Names follow `--catalog-<generic-component>-<domain>-<rule>` — at
  least three segments:
  - segment 1 is a **generic component family** and must not itself be
    a domain word;
  - segment 2 must be a known domain: `measure`, `typography`, `color`,
    `border`, `cursor`, `effect`, `motion`;
  - noncanonical scale abbreviations (`2xl`–`9xl`, `2xs`–`9xs`, `sm`,
    `md`, `lg`) are forbidden — use the BASE scale vocabulary;
  - no concrete application or component context words — a CATALOG
    token that names a real component directory or application concept
    leaks context into what must stay generic.

### Semantic aliases — `--semantic-*` or any unprefixed `--name`

- The value must be **exactly one plain `var(--catalog-*)` reference** —
  no literals, no fallbacks, no arithmetic, no composition of multiple
  tokens. A contextual alias selects one generic spec, nothing more.

### Terminal component CSS — ordinary declarations

- May consume **semantic-layer variables only**. Any `var()` pointing at
  `--primary-*`, `--dynamic-*`, `--base-*`, or `--catalog-*` directly
  bypasses the hierarchy.
- No design-composition functions: `calc()`, `min()`, `max()`,
  `clamp()`, `minmax()`, `color-mix()`, `repeat()` — compose the value
  upstream. This applies inside nested rules, pseudo-elements,
  keyframes, and responsive blocks.
- A **design/configuration property must be exactly one semantic
  `var()` reference** — colors, fills/strokes, opacity, dimensions,
  sizes, gaps, margins, padding, radii, borders, shadows, filters,
  perspective, typography (family/size/weight/stretch, line-height,
  letter/word spacing), durations, delays, easing, animation/transition
  specs, transforms and origins, grid tracks/areas/lines, `order`,
  `z-index`, cursor, SVG geometry (`x`, `y`, `r`, `rx`, `ry`, `cx`,
  `cy`, stroke widths/dashes), aspect ratios, object positions, scroll
  padding/margins, `content`, `quotes`, offsets, and similar
  design-bearing properties. The CSS-wide keywords `inherit`,
  `initial`, `unset`, `revert`, `revert-layer` may appear as the whole
  value.
- Other declarations may hold **structural grammar** keywords
  (`display`, `position`, `overflow`, `text-align`, `border-style`,
  `object-fit`, `flex-flow`, `writing-mode`, …) but never a hardcoded
  design literal — a shorthand or grammar property does not launder an
  embedded measure, `%`, hex color, or `rgb()/hsl()/lab()`-family
  function.
- Configurable numeric properties (`opacity`, `order`, `z-index`,
  `flex-grow`, `flex-shrink`, `flex-basis`, `column-count`, grid
  line/span selections, `orphans`, `widows`, `stroke-width`,
  `stroke-opacity`, `fill-opacity`) must flow through the token graph —
  no bare unitless numbers.
- Beyond the token audit, any component declaration containing a
  hardcoded design value — measure, color, gradient definition or
  component, font/text value, transition/animation duration, or easing
  — instead of a `var()` is rejected. Only the numeric literals `0`,
  `1`, `0%`, `100%` may stay literal.

## HTML

- No `<style>` elements — CSS lives in the project stylesheet.
- No inline `<script>` blocks — JavaScript lives in external files
  loaded with `src`.

## Linter and type-checker configuration

The configuration files are part of the contract — **keeping them
strict is mandatory, not cosmetic**. Weakening a rule to make code
pass is itself a violation (see Universal rules).

### `eslint.config.mjs` — JavaScript lint

- Flat config, **core rules only**, applied to `*.js`/`*.mjs`/`*.cjs`
  with `sourceType: "module"` and `ecmaVersion: "latest"` — modules
  are the project default; scripts are not.
- The browser/DOM/ES global set is declared explicitly and **read-only**
  — any global you use must exist in the config; add it there if it is
  genuinely new, never as a `/* global */` pragma.
- The ignores are fixed: `node_modules`, `*.d.ts`, `*.min.js`,
  `dist`/`build`/`coverage`, caches, `.venv`, `.history`, bundle output.
- The rule set is strict-error across the board. Highlights of what
  code must satisfy:
  - **No unused anything** — vars, args past `after-used`, caught
    errors, private class members, labels.
  - **No implicit or shadowed names** — `no-undef`, `no-redeclare`,
    `no-shadow`, no use-before-define for functions, variables, and
    classes (mutually recursive helpers are the only tolerated
    forward references, same scope only).
  - **Modern syntax required** — `no-var`, `prefer-const`,
    `prefer-bowman-callback`, `prefer-template`, `prefer-spread`,
    `prefer-rest-params`, `prefer-numeric-literals`,
    `prefer-object-spread`, `prefer-exponentiation-operator`,
    `logical-assignment-operators` (also inside `if`s), `prefer-object-has-own`,
    `prefer-regex-literals`, `prefer-promise-reject-errors`.
  - **No dynamic escape hatches** — `eval`, implied eval, `new
    Function`, `with`, `javascript:` URLs, `alert`/`confirm`/`prompt`,
    `debugger`.
  - **Honest comparisons and control flow** — `eqeqeq` (except `==
    null`), `use-isnan`, `valid-typeof`, no constant conditions or
    constant binary expressions, no assignment in conditionals, no
    fallthrough, `default-case-last`, `consistent-return`,
    `getter-return` (explicit), `grouped-accessor-pairs` (getter
    before setter), `no-else-return` (no `else` after `return`,
    `else if` allowed), `no-unreachable`/`no-unreachable-loop`,
    `for-direction`, `guard-for-in`.
  - **No sloppy patterns** — `one-var: never`, `no-multi-assign`,
    `no-sequences`, `no-inner-declarations`, `no-case-declarations`,
    `no-lone-blocks`, `no-loop-func`, `no-await-in-loop`,
    `radix` required, `symbol-description` required,
    `array-callback-return` (including `forEach`), `no-import-assign`,
    `no-new`/`no-new-wrappers`/`no-array-constructor`/
    `no-object-constructor`/`no-new-native-nonconstructor`,
    `no-throw-literal`, `require-atomic-updates`,
    `require-yield`, `dot-notation`.
  - **No warning comments** — `todo`, `fixme`, `xxx` anywhere in
    comments are errors. Track work elsewhere.
  - Exactly one rule is off: `no-weak-map`.

### `jsconfig.json` — JavaScript type-checking

- `allowJs` + `checkJs` + `noEmit` — **JavaScript is type-checked by
  TypeScript**; `.js`/`.mjs`/`.cjs` files must satisfy strict typing,
  not just parse.
- `strict: true` plus explicit strictness: `noImplicitAny`,
  `noImplicitReturns`, `noImplicitThis`, `noImplicitOverride`,
  `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
  `noPropertyAccessFromIndexSignature`, `noUnusedLocals`,
  `noUnusedParameters`, `useUnknownInCatchVariables`,
  `verbatimModuleSyntax`, `isolatedModules`,
  `noFallthroughCasesInSwitch`, `forceConsistentCasingInFileNames`,
  `allowUnreachableCode: false`, `allowUnusedLabels: false`,
  `skipLibCheck: false`, `maxNodeModuleJsDepth: 0`.
- Consequence for code: no implicit `any`, no unchecked index/record
  access, no unused locals/params, no unreachable code or fallthrough,
  `override` required where overridden, `unknown` (not `any`) in
  catches, type-only imports kept verbatim-separate.
- Target/module `ES2022`, `moduleResolution: "bundler"`, libs
  `ES2022` + `DOM` + `DOM.Iterable`, types `node`; `rootDirs` maps the
  served `web/packages` path — import paths must be written so they
  resolve under it.

### `pyproject.toml` — Python lint, type-check, test

- `[tool.ruff]`: `line-length = 79`, `target-version = "py313"`,
  `select = ["ALL"]` — **every Ruff rule enabled**; `ignore` is limited
  to docstring-style conflicts `D203`/`D213` (plus `COM812` where the
  formatter conflicts — nothing else may be added). `extend-exclude`
  is for generated/cache trees only (`build`, `dist`, `bundle`,
  `.history`, mutation caches) — never for hiding owned code.
- `[tool.basedpyright]`: `typeCheckingMode = "strict"`,
  `pythonVersion = "3.13"`. `executionEnvironments` may only narrow a
  **real subdirectory** (extra import roots, test-directory report
  relaxation) — an environment scoped to the project root is
  suppression and forbidden.
- `[tool.pyrefly]`: explicit `project-includes`/`project-excludes`/
  `search-path`/`python-version` — the same strict project, checked
  twice.
- `[tool.pytest.ini_options]`: `pythonpath` and `addopts` ignores are
  configuration, not a way to skip tests — `--ignore` entries name
  vendored/generated trees only.
- No `per-file-ignores`, no `extend-ignore`, no `report* = false`/
  `"none"` at project root — anywhere, ever.

### `stylelint.config.mjs` — CSS lint

- **Every disable is audited**: `reportDescriptionlessDisables`,
  `reportInvalidScopeDisables`, `reportNeedlessDisables` — a
  `stylelint-disable` comment must carry a description, be correctly
  scoped, and actually suppress something; gratuitous disables fail.
- Validity rules are all on: no unknown/deprecated properties,
  at-rules, descriptors, units, pseudo-classes/elements, functions,
  media features, or animations names; no duplicate properties,
  custom properties, selectors, or `@import`s; no empty blocks or
  sources; no invalid hex, `calc()` spacing errors, nonstandard
  gradient directions, malformed media queries, invalid named grid
  areas, irregular whitespace, `//` comments, or misplaced `@import`;
  no `!important` in keyframe declarations; no descending-specificity
  traps.
- Style policy is enforced structurally:
  - `declaration-no-important` — `!important` is banned outright.
  - `selector-max-id: 0` — no `#id` selectors.
  - `selector-max-universal: 0` — no `*` selectors.
  - `selector-max-specificity: 0,4,0` and `max-nesting-depth: 3` —
    keep selectors flat and shallow.
  - `number-max-precision: 6`.
  - Modern notation required: `color-hex-alpha: always` (alpha in hex
    where needed), `alpha-value-notation: percentage`,
    `color-function-notation: modern`, `hue-degree-notation: angle`,
    `media-feature-range-notation: context`, `selector-not-notation:
    complex`; `font-family` requires a generic family keyword and no
    duplicate names; custom elements may appear as type selectors.

## Executables and shebangs

Executable permission and shebang line must **always agree** — the two
are one fact, never independent:

- A Python file whose first line starts with `#!` is an entry point
  meant to run directly — it **must carry the executable bit**.
- A Python file without a shebang is a module, not an entry point — it
  **must not be executable**.
- The shebang must be the first line and must name an interpreter
  (`#!/usr/bin/env python3` for Python; `env` wrappers with `-S`,
  `-u`, `-C` and friends are understood). An empty `#!` line counts as
  a shebang with no interpreter — still an entry point, still
  executable-or-violation.
- Fix by completing the pair, either direction: mark the file
  executable **or** remove the shebang if it isn't really an entry
  point; add the canonical shebang for the file type **or** drop the
  executable bit if it was set by accident. The intent is a real
  entry point — not silence.
- Unreadable or unclassifiable files are themselves findings — a file
  the checker cannot inspect does not pass silently.

## Documentation

### Docstrings and JSDoc

Documentation must exist **and match the code**. Both requirements are
enforced — an empty stub or an outdated section is a violation, and
every finding carries the expected docstring skeleton.

**What must be documented:**

- Every **module** (a docstring at the top of each Python file).
- Every **class**, including nested classes.
- Every **function and method** — `def`, `async def`, `staticmethod`,
  `classmethod`, `property` — **including nested functions**, audited
  under `outer.<locals>.inner` qualified names just like top-level
  callables.
- Every JavaScript symbol — classes and their methods, function
  declarations, bowman functions, and variable-bound callables.

**What each docstring must contain:**

- A non-empty summary/description line — `"""` alone or an empty first
  line fails.
- `Args:`/`@param` entries covering **exactly** the parameter set:
  every signature parameter documented (`self`/`cls` excluded), and no
  documented name absent from the signature — renaming or deleting a
  parameter means updating the docstring.
- `Returns:`/`@returns` whenever the callable returns a value (a
  non-empty `return`, or a non-`None`/non-`NoReturn`/`Never`
  annotation).
- `Yields:`/`@yields` for every generator — a `yield`/`yield from`
  body with `Returns:` instead of `Yields:` (or neither) is a
  return/yield mismatch.
- `Raises:`/`@throws` covering **every** exception raised in the
  callable — no undocumented `raise`/`throw`, and no documented
  exception that is no longer raised (stale entries are findings).
- `Attributes:` on classes covering every **public attribute directly
  declared in the class body** (`name = ...` or `name: Type` at class
  level) — and no documented attribute that no longer exists. An
  annotated `self.x` inside a method is a legitimate entry but not a
  required one.
- Section entries in the expected `name (type): description` grammar —
  malformed entries are findings.

**Scope discipline:**

- Returns, raises, and yields are recognized **only in the callable's
  own scope** — a `raise` inside a nested function belongs to that
  nested function's docstring, not the parent's; the parent must
  document only what its own body raises/returns/yields. Each nested
  callable is held to the same contract independently.
- When auditing is restricted to the public API, `_private` Python
  names and `_`/`#` JavaScript names are skipped. Dunder methods are
  exempt except the lifecycle set `__init__`, `__call__`, `__enter__`,
  `__exit__`, `__aenter__`, `__aexit__`. Common JS lifecycle/builtin
  names (`constructor`, `render`, `mount`, `connectedCallback`,
  `toString`, `valueOf`, and the rest of the exempt-name set) do not
  require docs.
- A JSDoc block only counts when it directly precedes the symbol —
  an orphaned `/** ... */` elsewhere in the file does not document it.

### Directory inventories

- **Every directory** under the documented roots — `src`, `tests`,
  `examples`, `scripts`, `resources`, `docs` — carries a `README.md`.
  A directory without one is a violation.
- Each README must name **every direct entry in backticks**: files as
  `` `name` ``, subdirectories as `` `name/` `` (trailing slash
  required). One finding per undocumented entry — adding, removing, or
  renaming a file or subdirectory means updating the README in the
  same change.
- The README's own name is exempt from listing; `__init__.py` still
  counts as an entry that must be named.
- The skipped set is fixed: `__pycache__`, `.git`, `build`, `debug`,
  `dist`, `node_modules`, `site-packages`, `venv`, `DEBUG.txt`, hidden
  dot-entries, `*.egg-info`/`*.dist-info`, duplicate-copy names
  (`name 2.py`), and `.history` — none of these require documentation.
- A README may carry a **managed inventory block** delimited by
  `<!-- ...:inventory:start -->` / `<!-- ...:inventory:end -->`
  markers. Inside it lives the generated `## Directory contents`
  listing — `- [`name`](name) — <one-line description>` for files and
  `- [`name/`](name/README.md) — <description>` for subdirectories,
  rebuilt bottom-up from each file's docstring, JSDoc block, or first
  paragraph and each child README's introduction. Authored prose
  outside the markers is yours; **never hand-edit inside them** — the
  block is regenerated, and descriptions flow from real docstrings, so
  a stale entry means the source description is stale.

### Package documentation tree

- A documentation directory mirroring a source tree must reproduce the
  source's directory structure exactly, pruned at `__init__.py`
  boundaries — a directory holding `__init__.py` (or having no
  subdirectories) is a documentation **leaf**, and nothing below it is
  mirrored.
- Each leaf must contain a `DOCS.md` or a `DOCS_<UPPERCASE>.md`
  specialized file; specialized docs are allowed only on leaves.
  `README.md` is allowed anywhere; any other file is invalid.

## Tests

Three gates apply independently — a change passes only when all three
do.

### The suite must pass

- Every test must pass — pytest and `node:test` alike: no failures, no
  errors, no aborted runs. A test that cannot even run is a failure.
- Coverage is measured **with branches on** and covers **both Python
  and JavaScript**.

### The coverage gate

- **≥ 90% total coverage** across the measured source roots.
- **≥ 80% per source file** — no individual file may fall below the
  per-file floor even when the aggregate passes.
- The **mean file coverage** and the **validated-evidence total** each
  carry their own floor.
- Designated **critical paths** (control-plane modules) carry a stricter
  per-fragment floor — touching them requires proportionally more
  coverage.
- Files that no test reaches at all count at **0%**, not as absent —
  you cannot improve the numbers by keeping a file untested.

### The meaningful gate — behavior needs tests, not just coverage

Coverage alone is not sufficient. Every source file — **Python and
JavaScript** — that carries real behavior must be exercised by tests:

- Every source file no test reaches is semantically classified by how
  much it deserves testing, scored from evidence: behavioral
  statements, public behavioral surface, side effects, state mutations,
  branching and decision points, error/exception paths, computations,
  import-time behavior, nesting depth, and source size.
- **Structural** files (no behavioral evidence — declarations, re-export
  barrels, trivial constants) and **trivial** files may legitimately go
  untested.
- Any file that reaches **behavioral** level or above — the level a
  normal module earns through its public surface, branching, mutation,
  error paths, or complexity, let alone **complex** — is required to be
  covered: an unreached behavioral or complex file is a violation that
  only a real test resolves.
- Writing code whose behavior is only incidentally touched does not
  satisfy the gate — the classification follows the code's observable
  behavior, decisions, and side effects, not its filename or whether
  some test happens to execute its lines.

### Behavioral verification beyond unit tests

- Browser-facing behavior is verified by executable workflows: every
  flow must pass, with its assertions evaluated and its network
  expectations (URL, query, status, headers, body, timeout) satisfied.
- Visual output is verified against baselines: changed elements must be
  explained by the intended change — unexplained visual regressions
  fail.

## Sanctioned exemptions quick reference

| Exemption | Applies to |
|---|---|
| Bare `import` for allowlisted modules (`pytest`, `sys`, lint wrapper) | Python imports |
| `object` annotations | test files only |
| `COM812`, `D203`, `D213` in ignore lists | lint config |
| Type-checker environments scoped to a real subdirectory | type-check config |
| `property`/`cached_property`/`override`, dunders, virtual methods | method-binding rules |
| Foreign/positional-only/`pytest.mark.*` callees | keyword-call rule |
| `self`/`cls`, walrus, `TypeVar`, Enum members, lambdas, `_` | explicit-typing rule |
| Lifecycle dunders / exempt JS lifecycle names | doc requirements |
| Caches, dist, duplicate-copy names | README inventories |
| Runtime-derived style values | inline-style ban |
| `0`, `1`, `0%`, `100%` | CSS literal ban |

Suppression comments, `Any`, lint/type-check ignores, and hidden-path
evasion are **never** sanctioned escape hatches.
