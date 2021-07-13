History
=======

0.0.6 (2020-05-07)
------------------
* Initial internal release supporting basic features of Python;

0.0.7 (2020-05-07)
------------------
* Fix kernel install commmand for Windows;

0.0.8 (2020-05-08)
------------------
* Misc bug fixes;

0.0.9 (2020-05-08)
------------------
* Misc bug fixes;

0.0.10 (2020-05-08)
-------------------
* Rename kernel from `python3-nbsafety` to `nbsafety`;

0.0.11 (2020-05-08)
-------------------
* Readme formatting for PyPI;

0.0.12 (2020-05-08)
-------------------
* Readme formatting for PyPI;

0.0.13 (2020-05-08)
-------------------
* Refresh nodes w/ stale deps upon user override to avoid multiple of same warning;

0.0.14 (2020-05-08)
-------------------
* Fix cornercase bug for objects without __dict__ attribute (such as dictionaries);

0.0.15 (2020-05-11)
-------------------
* Fix bugs related to attr resolution for class attributes and add functionality to handle basic aliasing / mutation;

0.0.16 (2020-05-12)
-------------------
* Handle simple mutation deps for method calls (simple ast.Name args are added as deps);

0.0.17 (2020-05-12)
-------------------
* Add workaround for weird pandas attributes;

0.0.18 (2020-05-12)
-------------------
* Fix issue detecting completion of statement with calls inside of comprehensions;

0.0.19 (2020-05-12)
-------------------
* Don't require pandas;

0.0.20 (2020-05-12)
-------------------
* Minor stability fix;

0.0.21 (2020-05-12)
-------------------
* Increment cell numbers properly with %safety magic; other minor bugfixes;

0.0.22 (2020-05-12)
-------------------
* Increment cell number if precheck failed;

0.0.23 (2020-05-13)
-------------------
* Support AnnAssign (i.e. assignment with type annotations);

0.0.24 (2020-05-13)
-------------------
* Add logo;

0.0.25 (2020-05-13)
-------------------
* Hack to try and install kernel spec as post install script;

0.0.26 (2020-05-13)
-------------------
* More hacks to try and install kernel spec as post install script (switch to egg + use manifest);

0.0.27 (2020-05-13)
-------------------
* Give up on bdist_egg;

0.0.28 (2020-05-13)
-------------------
* Resort to hacky `atexit` command register call to facilitate post install script for kernel;

0.0.29 (2020-05-13)
-------------------
* Give up on post installation of kernel spec and try to include resources dir in package;

0.0.30 (2020-05-16)
-------------------
* Add front-end labextension to highlight stale and refresher cells;

0.0.31 (2020-05-18)
-------------------
* Bugfixes; version npm package and PyPI package in lockstep;

0.0.32 (2020-05-27)
-------------------
* Bugfixes; improve propagation of updated dependencies along namespace hierarchies;

0.0.33 (2020-05-27)
-------------------
* Minor usability improvements;

0.0.34 (2020-05-30)
-------------------
* Major bugfixes and improvements to dependency tracking;
* Fix bug that prevented attribute / subscript tracing on Python 3.6.

0.0.35 (2020-05-31)
-------------------
* Major bugfixes and improvements to the attribute / subscript tracer;
* Improvements to the logic for only propagating staleness past cell boundaries;

0.0.36 (2020-06-01)
-------------------
* Code quality improvements;
* Fixes to properly reference live args and kwargs inside of calls involving attributes and subscripts;

0.0.37 (2020-06-04)
-------------------
* Support fine-grained dependency edges for tuple unpacking for simple (non attribute / subscript) symbols;
* Bugfixes for args inside of nested function calls as well as for multiple inline function calls (eg f()());

0.0.38 (2020-06-05)
-------------------
* Bugfix: if returning from function, only pass up rvals if the ast statement is ast.Return;
* Handle dependencies from  one level of lambda capture properly;
* Fix not-displayed visual refresh cue for cells that threw exceptions to be refreshed if input contains an updated symbol;

0.0.39 (2020-06-08)
-------------------
* Bugfix for setting active scope correctly during ast.Store / AugStore context;
* Use versioneer to manage versioning and add bump_version.sh script;

0.0.40 (2020-06-08)
-------------------
* Accidental version release while automating build process;

0.0.41 (2020-06-18)
-------------------
* Fix bug where errors thrown when unimplemented ast.Slice or ast.ExtSlice encountered;
* Fix bug where assignment with empty rval could lead to version not getting bumped in provenance graph;

0.0.42 (2020-06-24)
-------------------
* Bugfixes;
* Efficiency compromise: don't trace multiple executions of same ast statement (e.g. if inside for loop);

0.0.43 (2020-06-28)
-------------------
* Bundle nbextension and auto-install at setup (along with kernel);

0.0.44 (2020-06-28)
-------------------
* Debug absent kernel.json when installing with pip;

0.0.45 (2020-06-28)
-------------------
* Explicitly add kernel.json to data_files in setup.py;

0.0.47 (2020-07-14)
-------------------
* Improve dependency tracking for tuple unpacking assignmengs;

0.0.48 (2020-07-22)
-------------------
* Only trace lambda call the first time during a map for performance;
* Faster computation of refresher cells by creating "inverted index" based on reaching defs;
* Reduce false positives in liveness checker;

0.0.49 (2020-07-27)
-------------------
* Remove altered Python logo to comply with PSF requirements;

0.0.50 (2020-08-25)
-------------------
* Significant stability improvements;

0.0.51 (2020-08-25)
-------------------
* Fix bug wherein non loop vars killed in comprehensions;

0.0.52 (2020-08-25)
-------------------
* Forgot to remove print statement;

0.0.53 (2020-08-29)
-------------------
* Fix pandas perf issue and other minor improvements;

0.0.54 (2020-10-11)
-------------------
* Propagate freshness to namespace children;
* Make jupyterlab a requirement;

0.0.57 (2021-12-01)
-------------------
* Various tracing improvements;
* Various analysis improvements;
* Fix for stack unwinding bug during trace reenabling;

0.0.59 (2021-03-10)
-------------------
* Various tracing improvements;
* Bugfix for tuple unpacking;

0.0.60 (2021-04-06)
-------------------
* Major improvements and bugfixes for lineage involving list, tuple, dict literals;
* Improvements to granuarity of dependency tracking for function calls;
* Improvements to dynamic symbol resolution;
* Improved handling for @property getter / setter methods;
* Fix some spurious warnings;
* Bugfix for statements involving `del`;

0.0.61 (2021-04-13)
-------------------
* Better handling for deletes;
* Reduce false positive highlights when updated symbol unchanged;
* Use new-style labextension, obviating need for separate `jupyter labextension install ...` command;

0.0.62 (2021-04-13)
-------------------
* Fix packaging issue;

0.0.64 (2021-05-17)
-------------------
* Various bugfixes and usability improvements;

0.0.66 (2021-05-17)
-------------------
* Hotfix for issue creating call arg data symbols;
* Security audit;

0.0.67 (2021-05-17)
-------------------
* Fix nbclassic bug;

0.0.68 (2021-05-18)
-------------------
* Actually fix nbclassic bug;
* Slight improvement to the lineno -> FunctionDef mapping (fixing some bugs);

0.0.69 (2021-05-22)
-------------------
* Minor logging fix;
* Minor no-op detection fix;
* Minor security fixes in npm packages;

0.0.70 (2021-06-05)
-------------------
* Improved slicing via timestamp-augmented liveness analysis;
* Bugfix to dedup slice computation;
* Bugfix to avoid resolving null symbol;
* Bugfix for improper class namespace registration;
* State transition bugfix for return from ClassDef;
* Misc improvements to mutations;
* Improved bookkeeping for list insertions / deletions;

0.0.71 (2021-06-27)
-------------------
* Add exceptions for general mutation rules;
* Fix return transition when first call happens outside notebook;
* Shuffle namespace symbols from old to new when namespace overwritten;

0.0.72 (2021-07-12)
-------------------
* Improve loop performance by better enforcing trace-once semantics;
* Bugfix for stack tracking when tracing reenabled;
