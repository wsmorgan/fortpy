from fortpy.parsers import DocStringParser, VariableParser, TypeParser, ModuleParser, ExecutableParser
from fortpy.elements import CustomType
import os
from time import clock
import xml.etree.ElementTree as ET
import fortpy.config
from serialize import Serializer
import sys
import tramp
import settings

config = sys.modules["config"]

class CodeParser(object):
    """Parses fortran files to extract child code elements and docstrings.

    :attr modulep: an instance of ModuleParser that can parse fortran module code.
    :attr modules: a dictionary of module code elements that have already been parsed.
    :attr basepaths: a list of folders to search in for dependency modules.
    :attr mappings: a list of modulename -> filename mappings to use in dependency searches.
    :attr verbose: specifies the level of detail in print outputs to console.
    :attr austere: when true, the python program quits if it can't find a module it
       is supposed to be loading; otherwise it just continues.
    :attr ssh: when true, the config settings for a remote SSH server are used instead
       of the local file system config.
    """
    
    def __init__(self, ssh=False, austere=False):
        """Initializes a module parser for parsing Fortran code files."""
        self.modulep = ModuleParser()
        self.modules = {}
        self.ssh = ssh
        self.austere = austere
       
        self.tramp = tramp.FileSupport()
        self.serialize = Serializer()
        
        if not settings.unit_testing_mode:
            if self.ssh:
                self.basepaths = config.ssh_codes
            else:
                self.basepaths = config.codes
            self.basepaths = [ self.tramp.expanduser(p, self.ssh) for p in self.basepaths ]
        else:
            self.basepaths = []

        if not settings.unit_testing_mode:
            if self.ssh:
                self.mappings = config.ssh_mappings
            else:
                self.mappings = config.mappings
        else:
            self.mappings = {}

        #A dictionary of filenames and the modules that they correspond
        #to if loaded
        self._modulefiles = {}
        #Keys are the full lowered paths, values are the short f90 names
        self._pathfiles = {}        
        #A list of module names that have already been parsed. This prevents code files
        #that have no modules from being parsed repeatedly.
        self._parsed = []
        self.verbose = False

        self.rescan()

    def __iter__(self):
        for key in self.modules.keys():
            yield self.modules[key]

    def list_dependencies(self, module, result):
        """Lists the names of all the modules that the specified module depends
        on."""
        if result is None:
            result = {}
        #We will try at least once to load each module that we don't have
        if module not in self.modules:
            self.load_dependency(module, True, True, False)

        if module in self.modules and module not in result:
            result[module] = self.modules[module].filepath
            for depend in self.modules[module].dependencies:
                name = depend.split(".")[0].lower()
                if name not in result:
                    self.list_dependencies(name, result)

        return result

    def _parse_dependencies(self, pmodules, dependencies, recursive, greedy):
        """Parses the dependencies of the modules in the list pmodules.

        :arg pmodules: a list of modules that were parsed from a *.f90 file.
        :arg dependencies: when true, the dependency's dependencies will be loaded.
        :arg recursive: specifies whether to continue loading dependencies to
           completion; i.e. up the chain until we have every module that any
           module needs to run.
        :arg greedy: when true, 
        """
        #See if we need to also load dependencies for the modules
        if dependencies:
            allkeys = [ module.name.lower() for module in pmodules ]
            for key in allkeys:
                for depend in self.modules[key].collection("dependencies"):
                    base = depend.split(".")[0]
                    if self.verbose and base.lower() not in self.modules:
                        print "DEPENDENCY: {}".format(base)
                    self.load_dependency(base, dependencies and recursive, recursive, greedy)

    def _parse_docstrings(self, filepath):
        """Looks for additional docstring specifications in the correctly named
        XML files in the same directory as the module."""        
        segs = filepath.split(".")
        segs.pop()
        xmlpath = ".".join(segs) + ".xml"
        if self.tramp.exists(xmlpath):
            xmlstring = self.tramp.readlines(xmlpath)
            self.modulep.docparser.parsexml(xmlstring, self.modules)
            
    def _parse_from_file(self, filepath, fname,
                         dependencies, recursive, greedy):
        """Parses the specified string to load the modules *from scratch* as
        opposed to loading pickled versions from the file cache."""
        #Now that we have the file contents, we can parse them using the parsers
        string = self.tramp.read(filepath)

        pmodules = self.modulep.parse(string, self)
        file_mtime = self.tramp.getmtime(filepath)

        for module in pmodules:
            module.change_time = file_mtime
            module.filepath = filepath
            self.modules[module.name.lower()] = module
            self._modulefiles[fname].append(module.name.lower())

        #There may be xml files for the docstrings that also need to be parsed.
        self._parse_docstrings(filepath)

        return pmodules

    def _check_parse_modtime(self, filepath, fname):
        """Checks whether the modules in the specified file path need
        to be reparsed because the file was changed since it was
        last loaded."""       
        file_mtime = self.tramp.getmtime(filepath)

        #If we have parsed this file and have its modules in memory, its
        #filepath will be in self._parsed. Otherwise we can load it from
        #file or from a cached pickle version.
        if filepath.lower() in self._parsed: 
            #Get the name of the first module in that file from the modulefiles
            #list. Find out when it was last modified.
            module_mtime = None
            if fname in self._modulefiles:
                modulename = self._modulefiles[fname][0]
                if modulename in self.modules:
                    module_mtime = self.modules[modulename].change_time

            if module_mtime is not None:
                if module_mtime < file_mtime:
                    #The file has been modified since we reloaded the module.
                    #Return the two times we used for the comparison so the
                    #module file can be reloaded.
                    return [module_mtime, file_mtime]
                else:
                    return None
        else:
            #The file has never been parsed by this CodeParser. We can
            #either do a full parse or a pickle load.
            return [file_mtime]

    def reparse(self, filepath):
        """Reparses the specified module file from disk, overwriting any
        cached representations etc. of the module."""
        #The easiest way to do this is to touch the file and then call
        #the regular parse method so that the cache becomes invalidated.
        self.tramp.touch(filepath)
        self.parse(filepath)

    def _add_current_codedir(self, path):
        """Adds the directory of the file at the specified path as a base
        path to find other files in.
        """
        dirpath = self.tramp.dirname(path)
        if dirpath not in self.basepaths:
            self.basepaths.append(dirpath)
            self.rescan()

    def parse(self, filepath, dependencies=False, recursive=False, greedy=False):
        """Parses the fortran code in the specified file.

        :arg dependencies: if true, all folder paths will be searched for modules
        that have been referenced but aren't loaded in the parser.
        :arg greedy: if true, when a module cannot be found using a file name
        of module_name.f90, all modules in all folders are searched."""
        #If we have already parsed this file path, we should check to see if the
        #module file has changed and needs to be reparsed.
        self._add_current_codedir(filepath)
        fname = filepath.split("/")[-1].lower()
        mtime_check = self._check_parse_modtime(filepath, fname)

        if mtime_check is None:
            return

        #Keep track of parsing times if we are running in verbose mode.
        if self.verbose:
            start_time = clock()
        if fname not in self._modulefiles:
            self._modulefiles[fname] = []

        #Check if we can load the file from a pickle instead of doing a time
        #consuming file system parse.
        pickle_load = False
        if len(mtime_check) == 1:
            #We use the pickler to load the file since a cached version might
            #be good enough.
            pmodules = self.serialize.load_module(filepath, mtime_check[0], self)
            
            if pmodules is not None:
                for module in pmodules:
                    self.modules[module.name.lower()] = module
                    self._modulefiles[fname].append(module.name.lower())
                pickle_load = True
            else:
                #We have to do a full load from the file system.
                pmodules = self._parse_from_file(filepath, fname,
                                                 dependencies, recursive, greedy)
        else:
            #We have to do a full load from the file system.
            pmodules = self._parse_from_file(filepath, fname,
                                  dependencies, recursive, greedy)

        #Add the filename to the list of files that have been parsed.
        self._parsed.append(filepath.lower())
        if not pickle_load:
            self.serialize.save_module(filepath, pmodules)

        if self.verbose:
            print "PARSED: {} modules in {} in {}".format(len(pmodules), fname, 
                                                          secondsToStr(clock() - start_time))
            for module in pmodules:
                print "\t{}".format(module.name)
            if len(pmodules) > 0:
                print ""

        self._parse_dependencies(pmodules, dependencies, recursive, greedy)

    def rescan(self):
        """Rescans the base paths to find new code files."""
        self._pathfiles = {}
        for path in self.basepaths:
                self.scan_path(path)

    def load_dependency(self, module_name, dependencies, recursive, greedy, ismapping = False):
        """Loads the module with the specified name if it isn't already loaded."""
        key = module_name.lower()
        if key not in self.modules:
            fkey = key + ".f90"
            if fkey in self._pathfiles:
                self.parse(self._pathfiles[fkey], dependencies, recursive)
            elif greedy:
                #The default naming doesn't match for this module
                #we will load all modules until we find the right
                #one
                self._load_greedy(key)
            elif key in self.mappings and self.mappings[key] in self._pathfiles:
                #See if they have a mapping specified to a code file for this module name.
                if self.verbose:
                    print "MAPPING: using {} as the file name for module {}".format(self.mappings[key], key)
                self.parse(self._pathfiles[self.mappings[key]], dependencies, recursive)
            else:
                print ("FATAL: could not find module {}. Enable greedy search or"
                       " add a module filename mapping.".format(key))
                if self.austere:
                    exit(1)

    def _load_greedy(self, module_name, dependencies, recursive):
        """Keeps loading modules in the filepaths dictionary until all have
        been loaded or the module is found."""
        found = module_name in self.modules
        allmodules = self._pathfiles.keys()
        i = 0

        while not found and i < len(allmodules):
            current = allmodules[i]
            if not current in self._modulefiles:
                #We haven't tried to parse this file yet
                self.parse(self._pathfiles[current], dependencies and recursive)                
                found = module_name in self.modules
            i += 1

    def scan_path(self, path, result = None):
        """Determines which valid fortran files reside in the base path.

        :arg path: the path to the folder to list f90 files in.
        :arg result: an optional dictionary to add results to in addition
        to populating the private member dictionary of the parser.
        """
        files = []
        
        #Find all the files in the directory
        for (dirpath, dirnames, filenames) in self.tramp.walk(path):
            files.extend(filenames)
            break

        #Filter them to find the fortran code files
        for fname in files:
            if os.path.splitext(fname)[1].lower() == ".f90":
                self._pathfiles[fname.lower()] = os.path.join(path, fname)
                if result is not None:
                    result[fname.lower()] = os.path.join(path, fname)
                    
    def type_search(self, basetype, symbolstr, origin):
        """Recursively traverses the module trees looking for the final
        code element in a sequence of %-separated symbols.

        :arg basetype: the type name of the first element in the symbol string.
        :arg symblstr: a %-separated list of symbols, e.g. this%sym%sym2%go.
        :arg origin: an instance of the Module class that started the request.
        """
        symbols = symbolstr.split("%")
        base = self.tree_find(basetype, origin, "types")

        #As long as we keep finding child objects, we can continue
        #until we run out of symbols in the list
        i = 1
        while isinstance(base, CustomType) and i < len(symbols): 
            #We will look inside the types members and executables
            if symbols[i] in base.members:
                #Types can have types inside of them. If the next symbol
                #is a member, we need to check if it is also a custom type
                base = base.members[symbols[i]]
                if base.is_custom:
                    base = self.tree_find(base.kind, origin, "types")
            elif symbols[i] in base.executables:
                base = base.executables[symbols[i]]
            #We want to keep iterating through until we find a non-type
            #which is either a non-type member or an executable
            i += 1

        return base                
            
    def tree_find(self, symbol, origin, attribute):
        """Finds the code element corresponding to specified symbol
        by searching all modules in the parser.

        :arg symbol: the name of the code element to find.
        :arg origin: an instance of a Module element who owns the text
          that is generate the find search.
        :arg attribute: one of ['dependencies', 'publics', 'members',
          'types', 'executables'] that specifies which collection
          in the module should house the symbol's element.
        """
        #The symbol must be accessible to the origin module, otherwise
        #it wouldn't compile. Start there, first looking at the origin
        #itself and then the other modules that it depends on.

        #Since we will be referring to this multiple times, might as 
        #well get a pointer to it.
        oattr = origin.collection(attribute)
        base = None

        if symbol in oattr:
            base = oattr[symbol]
            lorigin = origin
        else:
            for module in origin.dependencies:
                usespec = module.split(".")
                if len(usespec) > 1:
                    if usespec[1] == symbol:
                        #The dependency is to a specific element in the module,
                        #and it matches.
                        lorigin = self.get(usespec[0])
                    else:
                        lorigin = None
                else:
                    #The dependency is to the entire module!
                    lorigin = self.get(usespec[0])
            
                #If we have code for the origin, we can search for the
                #actual base object that we are interested in
                if lorigin is not None:
                    lattr = lorigin.collection(attribute)
                    if symbol in lattr:
                        base = lattr[symbol]
                        break

        #By now, we either have the item we were after or we don't have
        #code for the module it needs
        return base
    
    def get_executable(self, fullname):
        """Gets the executable corresponding to the specified full name.

        :arg fullname: a string with modulename.executable.
        """
        result = None
        [modname, exname] = fullname.split(".")
        module = self.get(modname)
        if module is not None:
            if exname in module.executables:
                result = module.executables[exname]

        return result

    def get(self, name):
        """Gets the module with the given name if it exists in
        this code parser."""
        if name in self.modules:
            return self.modules[name]
        else:
            return None

def secondsToStr(t):
    return "%d:%02d:%02d.%03d" % \
        reduce(lambda ll,b : divmod(ll[0],b) + ll[1:],
               [(t*1000,),1000,60,60])