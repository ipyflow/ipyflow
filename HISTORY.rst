History
=======

0.0.158 (2022-02-08)
--------------------
* Use proper input transformation for ipython syntax extensions during checking;

0.0.157 (2022-02-05)
--------------------
* Fix initialization race that caused execution to hang sometimes;
* Bugfix for hybrid dag liveness;

0.0.156 (2022-01-17)
--------------------
* Better support for backward slicing with ipywidgets;
* Support profile-based configuration;
* Slicing improvements for external calls that update module state;
* Misc bugfixes;

0.0.155 (2022-01-12)
--------------------
* Basic integration with %%capture magic;
* Improve ipywidgets integration;
* Support periodic content notifications to kernel;
* Ensure override_live_refs are included as static parents for dag scheduling;
* Misc bugfixes around cascading reactivity;
* Misc other bugfixes;

0.0.154 (2022-01-02)
--------------------
* Hide verbose / developer logging behind an environment variable shared with pyccolo;
* Improved ipywidgets integration;

0.0.153 (2022-12-23)
--------------------
* Misc bugfixes and improvements;
* Better handling of static / dynamic parents when out of order cell deps detected;

0.0.152 (2022-12-20)
--------------------
* Reactive state improvements;

0.0.151 (2022-12-18)
--------------------
* Bugfix for deadness detection;
* JupyterLab cmd+shift+enter hotkey for reactive / alt mode execution;

0.0.150 (2022-12-16)
--------------------
* Improve comprehension behavior;

0.0.149 (2022-12-14)
--------------------
* Improve garbage collection;
* Trace-once semantics for comprehensions;
* Misc liveness analysis improvements;
* Misc other bugfixes;

0.0.148 (2022-12-09)
--------------------
* Revert previous change;
* Distinguish between cascading / non updated reactive symbols;

0.0.147 (2022-12-09)
--------------------
* Treat attrsub value as live during nested symbol assignment;

0.0.146 (2022-12-08)
--------------------
* Another bugfix around reactive / cascading reactive modifiers;

0.0.144 (2022-12-07)
--------------------
* Bugfix that allows new threads to be spawned more reliably;
* Better handling around reactive / cascading reactive modifiers;
* Better handling around readiness computation for hybrid liveness / dag scheduling;

0.0.143 (2022-12-04)
--------------------
* Bugfixes around reactivity-blocking syntax extension;
* Integration for ipywidgets;
* Remove restriction on ipython version;

0.0.142 (2022-11-23)
--------------------
* Couple of minor bugfixes;

0.0.141 (2022-11-23)
--------------------
* Relax gc thresholds to improve perf;

0.0.140 (2022-11-22)
--------------------
* Improved support for Modin dataframes;

0.0.139 (2022-11-22)
--------------------
* Better handling of namespace symbol contributions to slices;

0.0.138 (2022-11-21)
--------------------
* Pin pyccolo to 0.0.39 exactly prevent forward compat issues;
* Fix a couple of hybrid-liveness-dag issues / corner cases;
* Stop reactive execution when exception is encountered;
* Use liveness_based exec_schedule for classic notebook frontend;

0.0.137 (2022-11-19)
--------------------
* Use pyccolo >= 0.0.39 for better syntax augmentation;

0.0.136 (2022-11-16)
--------------------
* Disable tracing during magics;
* Disable tracing below a certain call depth of external code;

0.0.135 (2022-11-15)
--------------------
* Infinite recursion corner case hotfix;

0.0.134 (2022-11-15)
--------------------
* Better handling for _ symbol;
* Cinder failsafe in symbol resync;

0.0.133 (2022-11-14)
--------------------
* Use non-ipyflow execution path for empty cells;
* Improve DAG scheduler with symbol info on edges;
* Add hybrid DAG + liveness based exec schedule and make default;
* Fix upsert_symbol stmt number bug;
* Allow comm open message to set configuration;

0.0.132 (2022-11-08)
--------------------
* Actually fix cyclic waiting check bug;

0.0.131 (2022-11-07)
--------------------
* Bugfix for cyclic waiting check;
* Only process previously-executed cells by default;

0.0.130 (2022-11-06)
--------------------
* Make core api functions directly importable from ipyflow;

0.0.129 (2022-11-06)
--------------------
* Configurable reactive highlights;
* Use typescript 4.3.5 (compatible with JupyterLab 3)
* Update logos;

0.0.128 (2022-10-31)
--------------------
* Misc fixes;

0.0.127 (2022-10-28)
--------------------
* Add call symbols as deps during namespace unpack assign;
* Improved handling / tolerance around execution counters;

0.0.126 (2022-10-26)
--------------------
* Bugfixes for call scopes and symbol tables (better global / nonlocal handling);
* Bugfix to get working on Python 3.11

0.0.125 (2022-10-23)
--------------------
* Scaffolding for watchpoint functionality;
* stderr / stdout API functions for accessing cell outputs;

0.0.124 (2022-10-13)
--------------------
* Bugfixes around module usage and timestamps;

0.0.123 (2022-10-12)
--------------------
* Small bugfix to ensure import statements kill symbols during liveness analysis;

0.0.122 (2022-10-12)
--------------------
* Small bugfix for dependency inference in attributes / subscripts;

0.0.121 (2022-10-12)
--------------------
* Fixes and improvements for dataflow annotation dsl;
* Api methods for (r)deps, (r)users, timestamp, code

0.0.120 (2022-10-01)
--------------------
* Allow ImportFrom to kill symbols during static analysis;

0.0.119 (2022-09-27)
--------------------
* Important bugfixes;

0.0.118 (2022-09-27)
--------------------
* Misc bugfixes;
* Improve code for external call handlers;
* Better handling for module symbols;

0.0.117 (2022-07-03)
--------------------
* Misc bugfixes;

0.0.116 (2022-07-02)
--------------------
* Bump pyccolo to a version with perf improvements for imports;
* Lazily import mutation special case modules;
* Fix more versioneer issues;

0.0.112 (2022-06-30)
--------------------
* Keep ipyflow and ipyflow-core versions in lock-step;

0.0.111 (2022-06-30)
--------------------
* Fix some versioneer issues;
* Fix a bug related to readiness for in-order semantics;

0.0.109 (2022-06-14)
--------------------
* Add comm handler for refresh symbols;
* Add comm handler for upserting symbol;
* Add comm handler for registering dynamic comm handlers;
* Make comm handlers all send at least an ack response;
* Disable syntax transforms for magic cells;
* Allow syntax transforms to be toggled via a magic;
# Exclude garbage symbols from user-accessible;

0.0.106 (2022-06-10)
--------------------
* Make cascading reactivity also work for not-yet-executed cells;

0.0.105 (2022-06-09)
--------------------
* Treat cells with non-resolvable live refs as waiting;

0.0.104 (2022-06-09)
--------------------
* Make in_order semantics the default;
* Model unexecuted cells as well as executed ones;

0.0.103 (2022-06-08)
--------------------
* Add get_code magic;

0.0.102 (2022-06-06)
--------------------
* Stdout / stderr tee utilities delegate non-critical attributes;

0.0.99 (2022-06-06)
-------------------
* Fix serialization bug that prevented in-order semantics from working properly;

0.0.98 (2022-06-05)
-------------------
* Add ability to register custom comm handlers;
* Fix lazy import ImportError issue (possibly manifesting on cinder);

0.0.97 (2022-05-30)
-------------------
* Add api package with 'lift' function for resolving argument to DataSymbol;
* Fixes for pyccolo 0.0.28 breaking changes;

0.0.96 (2022-05-22)
-------------------
* Add optional capability for linting unsafe order usages;

0.0.93 (2022-05-16)
-------------------
* Add line magic to run with syntax transforms only, and no tracing;

0.0.92 (2022-05-16)
-------------------
* Properly pass call_scope and function definition nodes between aliasing symbols;

0.0.91 (2022-05-04)
-------------------
* Cascading reactivity for namespace symbols;

0.0.90 (2022-05-01)
-------------------
* Reactivity works for dirty cells;
* Change scheduling nomenclature + line magics (safety -> flow);

0.0.85 (2022-03-17)
-------------------
* Fixes for pyccolo 0.0.22 breaking changes;
* Add out-of-order warnings for strict / in_order semantics;
* Upsert both df["col"] and df.col for pandas dataframes;
* Misc js security fixes;

0.0.84 (2022-03-02)
-------------------
* Skip static checking when dataflow tracing not enabled;
* Minor bugfix for dynamic slicing with tuple assignment;
* Use ipython<8.0.0 for performance reasons, pending further investigation;
* Start factoring out pyccolo-specific stuff into the kernel subclass for generic use later;

0.0.83 (2022-02-14)
-------------------
* Add register / deregister subcommands for other Pyccolo tracers;
* Keep tracing context active between cell executions;

0.0.81 (2022-01-26)
-------------------
* Use pyccolo for instrumentation;
* Fix to not crash on immediately-called lambdas during analysis;

0.0.80 (2021-10-26)
-------------------
* Implement reactive symbols;
* Separate concept of 'schedule' from flow order;
* Add experimental dag and strict schedules;
* Misc bug fixes;

0.0.79 (2021-10-06)
-------------------
* Improve detection of whether cell is newly fresh;

0.0.78 (2021-10-05)
-------------------
* Expose in-order and any-order flow semantics via line magic;

0.0.77 (2021-10-04)
-------------------
* Fix regression that caused kernel to crash on syntax errors;

0.0.76 (2021-09-29)
-------------------
* Get rid of accidental debug logging statement;

0.0.75 (2021-09-28)
-------------------
* Fix state transition bug where current scope not restored;
* Make checker results strongly typed;
* No more warning for stale usages; just show the highlight;
* Various fixes to reduce intrusiveness (no attribute / subscript dereferencing at check time);
* Bump frontend dependencies to more secure versions;

0.0.74 (2021-09-24)
-------------------
* Misc bugfixes and improvements;
* Fix bug where function scope overridden on redefinition;
* Handle global / nonlocal state;
* Get rid of unnecessary frontend dep, thereby fixing retrolab compat issue;

0.0.73 (2021-09-04)
-------------------
* Misc bugfixes and improvements;
* Ignore mutating calls when determining fresh cells;
* Experimental reactivity prototype;

0.0.72 (2021-07-12)
-------------------
* Improve loop performance by better enforcing trace-once semantics;
* Bugfix for stack tracking when tracing reenabled;

0.0.71 (2021-06-27)
-------------------
* Add exceptions for general mutation rules;
* Fix return transition when first call happens outside notebook;
* Shuffle namespace symbols from old to new when namespace overwritten;

0.0.70 (2021-06-05)
-------------------
* Improved slicing via timestamp-augmented liveness analysis;
* Bugfix to dedup slice computation;
* Bugfix to avoid resolving null symbol;
* Bugfix for improper class namespace registration;
* State transition bugfix for return from ClassDef;
* Misc improvements to mutations;
* Improved bookkeeping for list insertions / deletions;

0.0.69 (2021-05-22)
-------------------
* Minor logging fix;
* Minor no-op detection fix;
* Minor security fixes in npm packages;

0.0.68 (2021-05-18)
-------------------
* Actually fix nbclassic bug;
* Slight improvement to the lineno -> FunctionDef mapping (fixing some bugs);

0.0.67 (2021-05-17)
-------------------
* Fix nbclassic bug;

0.0.66 (2021-05-17)
-------------------
* Hotfix for issue creating call arg data symbols;
* Security audit;

0.0.64 (2021-05-17)
-------------------
* Various bugfixes and usability improvements;

0.0.62 (2021-04-13)
-------------------
* Fix packaging issue;

0.0.61 (2021-04-13)
-------------------
* Better handling for deletes;
* Reduce false positive highlights when updated symbol unchanged;
* Use new-style labextension, obviating need for separate `jupyter labextension install ...` command;

0.0.60 (2021-04-06)
-------------------
* Major improvements and bugfixes for lineage involving list, tuple, dict literals;
* Improvements to granuarity of dependency tracking for function calls;
* Improvements to dynamic symbol resolution;
* Improved handling for @property getter / setter methods;
* Fix some spurious warnings;
* Bugfix for statements involving `del`;

0.0.59 (2021-03-10)
-------------------
* Various tracing improvements;
* Bugfix for tuple unpacking;

0.0.57 (2021-12-01)
-------------------
* Various tracing improvements;
* Various analysis improvements;
* Fix for stack unwinding bug during trace reenabling;

0.0.54 (2020-10-11)
-------------------
* Propagate freshness to namespace children;
* Make jupyterlab a requirement;

0.0.53 (2020-08-29)
-------------------
* Fix pandas perf issue and other minor improvements;

0.0.52 (2020-08-25)
-------------------
* Forgot to remove print statement;

0.0.51 (2020-08-25)
-------------------
* Fix bug wherein non loop vars killed in comprehensions;

0.0.50 (2020-08-25)
-------------------
* Significant stability improvements;

0.0.49 (2020-07-27)
-------------------
* Remove altered Python logo to comply with PSF requirements;

0.0.48 (2020-07-22)
-------------------
* Only trace lambda call the first time during a map for performance;
* Faster computation of refresher cells by creating "inverted index" based on reaching defs;
* Reduce false positives in liveness checker;

0.0.47 (2020-07-14)
-------------------
* Improve dependency tracking for tuple unpacking assignmengs;

0.0.45 (2020-06-28)
-------------------
* Explicitly add kernel.json to data_files in setup.py;

0.0.44 (2020-06-28)
-------------------
* Debug absent kernel.json when installing with pip;

0.0.43 (2020-06-28)
-------------------
* Bundle nbextension and auto-install at setup (along with kernel);

0.0.42 (2020-06-24)
-------------------
* Bugfixes;
* Efficiency compromise: don't trace multiple executions of same ast statement (e.g. if inside for loop);

0.0.41 (2020-06-18)
-------------------
* Fix bug where errors thrown when unimplemented ast.Slice or ast.ExtSlice encountered;
* Fix bug where assignment with empty rval could lead to version not getting bumped in provenance graph;

0.0.40 (2020-06-08)
-------------------
* Accidental version release while automating build process;

0.0.39 (2020-06-08)
-------------------
* Bugfix for setting active scope correctly during ast.Store / AugStore context;
* Use versioneer to manage versioning and add bump_version.sh script;

0.0.38 (2020-06-05)
-------------------
* Bugfix: if returning from function, only pass up rvals if the ast statement is ast.Return;
* Handle dependencies from  one level of lambda capture properly;
* Fix not-displayed visual refresh cue for cells that threw exceptions to be refreshed if input contains an updated symbol;

0.0.37 (2020-06-04)
-------------------
* Support fine-grained dependency edges for tuple unpacking for simple (non attribute / subscript) symbols;
* Bugfixes for args inside of nested function calls as well as for multiple inline function calls (eg f()());

0.0.36 (2020-06-01)
-------------------
* Code quality improvements;
* Fixes to properly reference live args and kwargs inside of calls involving attributes and subscripts;

0.0.35 (2020-05-31)
-------------------
* Major bugfixes and improvements to the attribute / subscript tracer;
* Improvements to the logic for only propagating staleness past cell boundaries;

0.0.34 (2020-05-30)
-------------------
* Major bugfixes and improvements to dependency tracking;
* Fix bug that prevented attribute / subscript tracing on Python 3.6.

0.0.33 (2020-05-27)
-------------------
* Minor usability improvements;

0.0.32 (2020-05-27)
-------------------
* Bugfixes; improve propagation of updated dependencies along namespace hierarchies;

0.0.31 (2020-05-18)
-------------------
* Bugfixes; version npm package and PyPI package in lockstep;

0.0.30 (2020-05-16)
-------------------
* Add front-end labextension to highlight stale and refresher cells;

0.0.29 (2020-05-13)
-------------------
* Give up on post installation of kernel spec and try to include resources dir in package;

0.0.28 (2020-05-13)
-------------------
* Resort to hacky `atexit` command register call to facilitate post install script for kernel;

0.0.27 (2020-05-13)
-------------------
* Give up on bdist_egg;

0.0.26 (2020-05-13)
-------------------
* More hacks to try and install kernel spec as post install script (switch to egg + use manifest);

0.0.25 (2020-05-13)
-------------------
* Hack to try and install kernel spec as post install script;

0.0.24 (2020-05-13)
-------------------
* Add logo;

0.0.23 (2020-05-13)
-------------------
* Support AnnAssign (i.e. assignment with type annotations);

0.0.22 (2020-05-12)
-------------------
* Increment cell number if precheck failed;

0.0.21 (2020-05-12)
-------------------
* Increment cell numbers properly with %safety magic; other minor bugfixes;

0.0.20 (2020-05-12)
-------------------
* Minor stability fix;

0.0.19 (2020-05-12)
-------------------
* Don't require pandas;

0.0.18 (2020-05-12)
-------------------
* Fix issue detecting completion of statement with calls inside of comprehensions;

0.0.17 (2020-05-12)
-------------------
* Add workaround for weird pandas attributes;

0.0.16 (2020-05-12)
-------------------
* Handle simple mutation deps for method calls (simple ast.Name args are added as deps);

0.0.15 (2020-05-11)
-------------------
* Fix bugs related to attr resolution for class attributes and add functionality to handle basic aliasing / mutation;

0.0.14 (2020-05-08)
-------------------
* Fix cornercase bug for objects without __dict__ attribute (such as dictionaries);

0.0.13 (2020-05-08)
-------------------
* Refresh nodes w/ stale deps upon user override to avoid multiple of same warning;

0.0.12 (2020-05-08)
-------------------
* Readme formatting for PyPI;

0.0.11 (2020-05-08)
-------------------
* Readme formatting for PyPI;

0.0.10 (2020-05-08)
-------------------
* Rename kernel from `python3-nbsafety` to `nbsafety`;

0.0.9 (2020-05-08)
------------------
* Misc bug fixes;

0.0.8 (2020-05-08)
------------------
* Misc bug fixes;

0.0.7 (2020-05-07)
------------------
* Fix kernel install commmand for Windows;

0.0.6 (2020-05-07)
------------------
* Initial internal release supporting basic features of Python;

