
// import "jqueryui";
//
// declare global {
//   interface Window {
//     midas: MidasContainer;
//     selectionShelf: SelectionShelf;
//     profilerShelf: ProfilerShelf;
//   }
// }

__non_webpack_require__([
  'base/js/namespace'
], function load_ipython_extension(Jupyter: any) {
    console.log(
        'This is the current notebook application instance:',
        Jupyter.notebook
    );
});