""" DaCe Python parsing functionality and entry point to Python frontend. """
from __future__ import print_function
from collections import OrderedDict
from functools import wraps
import inspect
import ast
import copy
import sys
import numpy

from dace import data, symbolic, dtypes
from dace.config import Config
from dace.frontend.python import astparser, astutils, depanalysis
from dace.sdfg import SDFG
from dace.graph import labeling


def _create_datadescriptor(obj):
    """ Creates a data descriptor from various types of objects.
        @see: dace.data.Data
    """
    if isinstance(obj, data.Data):
        return obj

    try:
        return obj.descriptor
    except AttributeError:
        if isinstance(obj, numpy.ndarray):
            return data.Array(
                dtype=dtypes.typeclass(obj.dtype.type), shape=obj.shape)
        if symbolic.issymbolic(obj):
            return data.Scalar(symbolic.symtype(obj))
        if isinstance(obj, dtypes.typeclass):
            return data.Scalar(obj)
        return data.Scalar(dtypes.typeclass(type(obj)))


def _get_type_annotations(f, f_argnames, decorator_args):
    """ Obtains types from decorator or from type annotations in a function. 
    """
    type_annotations = {}
    if hasattr(f, '__annotations__'):
        type_annotations.update(f.__annotations__)

    # Type annotation conditions
    has_args = len(decorator_args) > 0
    has_annotations = len(type_annotations) > 0
    if 'return' in type_annotations:
        raise TypeError('DaCe programs do not have a return type')
    if has_args and has_annotations:
        raise SyntaxError('DaCe programs can only have decorator arguments ' +
                          '(\'@dace.program(...)\') or type annotations ' +
                          '(\'def program(arr: type, ...)\'), but not both')

    # Alert if there are any discrepancies between annotations and arguments
    if has_args:
        # Make sure all arguments are annotated
        if len(decorator_args) != len(f_argnames):
            raise SyntaxError(
                'Decorator arguments must match number of DaCe ' +
                'program parameters (expecting ' + str(len(f_argnames)) + ')')
        # Return arguments and their matched decorator annotation
        return {
            k: _create_datadescriptor(v)
            for k, v in zip(f_argnames, decorator_args)
        }
    elif has_annotations:
        # Make sure all arguments are annotated
        if len(type_annotations) != len(f_argnames):
            raise SyntaxError(
                'Either none or all DaCe program parameters must ' +
                'have type annotations')
    return {k: _create_datadescriptor(v) for k, v in type_annotations.items()}


def _get_argnames(f):
    """ Returns a Python function's argument names. """
    try:
        return inspect.getfullargspec(f).args
    except AttributeError:
        return inspect.getargspec(f).args


def _compile_module(s, name='<string>'):
    """ Compiles a string representing a python module (file or code) and
        returns the resulting global objects as a dictionary mapping name->val.
        @param name: Optional name for better error message handling.
    """

    gen_module = {}
    code = compile(s, name, 'exec')
    exec(code, gen_module)
    return gen_module


def parse_from_file(filename, *compilation_args):
    """ Try to parse all DaCe programs in `filename` and return a list of
        obtained SDFGs. Raises exceptions in case of compilation errors.
        Also accepts optional compilation arguments containing types and symbol
        values.
    """

    with open(filename, 'r') as f:
        code = f.read()

    mod = _compile_module(code, filename)

    programs = [
        program for program in mod.values()
        if isinstance(program, DaceProgram)
    ]

    return [parse_function(p, *compilation_args) for p in programs]


def parse_from_function(function, *compilation_args, strict=None):
    """ Try to parse a DaceProgram object and return the `dace.SDFG` object
        that corresponds to it.
        @param function: DaceProgram object (obtained from the `@dace.program`
                         decorator).
        @param compilation_args: Various compilation arguments e.g. dtypes.
        @param strict: Whether to apply strict transformations or not (None
                       uses configuration-defined value). 
        @return: The generated SDFG object.
    """
    if not isinstance(function, DaceProgram):
        raise TypeError(
            'Function must be of type dace.frontend.python.DaceProgram')

    # Obtain parsed DaCe program
    pdp, modules = function.generate_pdp(*compilation_args)

    # Create an empty SDFG
    sdfg = SDFG(pdp.name)

    sdfg.set_sourcecode(pdp.source, 'python')

    # Populate SDFG with states and nodes, according to the parsed DaCe program

    # 1) Inherit dependencies and inject tasklets
    # 2) Traverse program graph and recursively split into states,
    #    annotating edges with their transition conditions.
    # 3) Add arrays, streams, and scalars to the SDFG array store
    # 4) Eliminate empty states with no conditional outgoing transitions
    # 5) Label states in topological order
    # 6) Construct dataflow graph for each state

    # Step 1)
    for primitive in pdp.children:
        depanalysis.inherit_dependencies(primitive)

    # Step 2)
    state_primitives = depanalysis.create_states_simple(pdp, sdfg)

    # Step 3)
    for dataname, datadesc in pdp.all_arrays().items():
        sdfg.add_datadesc(dataname, datadesc)

    # Clear "nested" symbols created as a result of AST parsing
    sdfg._symbols = set()
    for symname, symtype in pdp.argtypes.items():
        if (symname not in sdfg.arrays
                or isinstance(sdfg.arrays[symname], data.Scalar)):
            sdfg.add_symbol(symname, symtype.dtype, override_dtype=True)

    # Step 5)
    stateList = sdfg.topological_sort(sdfg.start_state)
    for i, state in enumerate(stateList):
        if state.label is None or state.label == "":
            state.set_label("s" + str(i))

    # Step 6)
    for i, state in enumerate(stateList):
        depanalysis.build_dataflow_graph(sdfg, state, state_primitives[state],
                                         modules)

    # Fill in scope entry/exit connectors
    sdfg.fill_scope_connectors()

    # Memlet propagation
    if sdfg.propagate:
        labeling.propagate_labels_sdfg(sdfg)

    # Drawing the SDFG before strict transformations
    sdfg.draw_to_file(recursive=True)

    # Apply strict transformations automatically
    if (strict == True
            or (strict is None
                and Config.get_bool('optimizer', 'automatic_state_fusion'))):
        sdfg.apply_strict_transformations()

    # Drawing the SDFG (again) to a .dot file
    sdfg.draw_to_file(recursive=True)

    # Validate SDFG
    sdfg.validate()

    return sdfg


class DaceProgram:
    """ A data-centric program object, obtained by decorating a function with
        `@dace.program`. """

    def __init__(self, f, args, kwargs):
        self.f = f
        self.args = args
        self.kwargs = kwargs
        self._name = f.__name__
        self.argnames = _get_argnames(f)
        if self.argnames is None:
            self.argnames = []

    @property
    def name(self):
        return self._name

    def to_sdfg(self, *args, strict=None):
        """ Parses the DaCe function into an SDFG. """
        return parse_from_function(self, *args, strict=strict)

    def compile(self, *args, strict=None, specialize=None):
        """ Convenience function that parses and compiles a DaCe program. """
        sdfg = parse_from_function(self, *args, strict=strict)
        return sdfg.compile(specialize=specialize)

    def __call__(self, *args, strict=None, specialize=None):
        """ Convenience function that parses, compiles, and runs a DaCe 
            program. """
        binaryobj = self.compile(*args, strict=strict, specialize=specialize)
        # Add named arguments to the call
        kwargs = {aname: arg for aname, arg in zip(self.argnames, args)}
        # Update arguments with symbols in data shapes
        kwargs.update({
            sym: symbolic.symbol(sym).get()
            for arg in args
            for sym in (symbolic.symlist(arg.descriptor.shape) if hasattr(
                arg, 'descriptor') else [])
        })
        return binaryobj(**kwargs)

    def generate_pdp(self, *compilation_args):
        """ Generates the parsed AST representation of a DaCe program.
            @param compilation_args: Various compilation arguments e.g., dtypes.
            @return: A 2-tuple of (program, modules), where `program` is a 
                     `dace.astnodes._ProgramNode` representing the parsed DaCe 
                     program, and `modules` is a dictionary mapping imported 
                     module names to their actual module names (for maintaining
                     import aliases).
        """
        dace_func = self.f
        args = self.args

        # If exist, obtain type annotations (for compilation)
        argtypes = _get_type_annotations(dace_func, self.argnames, args)

        # Parse argument types from call
        if not argtypes:
            if not compilation_args:
                raise SyntaxError(
                    'DaCe program compilation requires either type annotations '
                    'or arrays')

            # Parse compilation arguments
            if len(compilation_args) != len(self.argnames):
                raise SyntaxError(
                    'Arguments must match DaCe program parameters (expecting '
                    '%d)' % len(self.argnames))
            argtypes = {
                k: _create_datadescriptor(v)
                for k, v in zip(self.argnames, compilation_args)
            }
        #############################################

        # Parse allowed global variables
        # (for inferring types and values in the DaCe program)
        global_vars = {
            k: v
            for k, v in dace_func.__globals__.items() if dtypes.isallowed(v)
        }
        modules = {
            k: v.__name__
            for k, v in dace_func.__globals__.items() if dtypes.ismodule(v)
        }
        modules['builtins'] = ''

        # Add symbols as globals with their actual names (sym_0 etc.)
        global_vars.update({
            v.name: v
            for k, v in global_vars.items() if isinstance(v, symbolic.symbol)
        })

        # Add keyword arguments as additional globals
        global_vars.update(
            {k: v
             for k, v in self.kwargs.items() if dtypes.isallowed(v)})

        # Parse AST to create the SDFG
        pdp = astparser.parse_dace_program(dace_func, argtypes, global_vars,
                                           modules)

        # Transform parsed DaCe code into a DaCe program (Stateful DFG)
        return pdp, modules
