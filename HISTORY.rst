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
------------------
* Rename kernel from `python3-nbsafety` to `nbsafety`;

0.0.11 (2020-05-08)
------------------
* Readme formatting for PyPI;

0.0.12 (2020-05-08)
------------------
* Readme formatting for PyPI;

0.0.13 (2020-05-08)
------------------
* Refresh nodes w/ stale deps upon user override to avoid multiple of same warning;

0.0.14 (2020-05-08)
------------------
* Fix cornercase bug for objects without __dict__ attribute (such as dictionaries);

0.0.15 (2020-05-11)
------------------
* Fix bugs related to attr resolution for class attributes and add functionality to handle basic aliasing / mutation;

0.0.16 (2020-05-12)
------------------
* Handle simple mutation deps for method calls (simple ast.Name args are added as deps);

0.0.17 (2020-05-12)
------------------
* Add workaround for weird pandas attributes;

0.0.18 (2020-05-12)
------------------
* Fix issue detecting completion of statement with calls inside of comprehensions;

0.0.19 (2020-05-12)
------------------
* Don't require pandas;

0.0.20 (2020-05-12)
------------------
* Minor stability fix;

0.0.21 (2020-05-12)
------------------
* Increment cell numbers properly with %safety magic; other minor bugfixes;

0.0.22 (2020-05-12)
------------------
* Increment cell number if precheck failed;

0.0.23 (2020-05-13)
------------------
* Support AnnAssign (i.e. assignment with type annotations);

0.0.24 (2020-05-13)
------------------
* Add logo;

0.0.25 (2020-05-13)
------------------
* Hack to try and install kernel spec as post install script;

0.0.26 (2020-05-13)
------------------
* More hacks to try and install kernel spec as post install script (switch to egg + use manifest);
