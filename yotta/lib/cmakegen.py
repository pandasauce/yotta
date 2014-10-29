# Copyright 2014 ARM Limited
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for details.

# standard library modules, , ,
import string
import os
import logging

# Cheetah, pip install cheetah, string templating, MIT
import Cheetah.Template

# fsutils, , misc filesystem utils, internal
import fsutils
# validate, , validate various things, internal
import validate

CMakeLists_Template = '''
# NOTE: This file is generated by yotta: changes will be overwritten!

#if $toplevel
cmake_minimum_required(VERSION 2.8)

# always use the CMAKE_MODULE_PATH-provided .cmake files, even when including
# from system directories:
cmake_policy(SET CMP0017 OLD)

# toolchain file for $target_name
set(CMAKE_TOOLCHAIN_FILE $toolchain_file)

$set_targets_like
#end if

project($component_name)

# include own root directory
#echo $include_own_dir

# include root directories of all components we depend on (directly and
# indirectly)
$include_root_dirs

# recurse into dependencies that aren't built elsewhere
$add_depend_subdirs


# Some components (I'm looking at you, libc), need to export system header
# files with no prefix, these directories are listed in the component
# description files:
$include_sys_dirs

# And others (typically CMSIS implementations) need to export non-system header
# files. Please don't use this facility. Please. It's much, much better to fix
# implementations that import these headers to import them using the full path.
$include_other_dirs

# CMake doesn't have native support for Objective-C specific flags, these are
# specified by any depended-on objc runtime using secret package properties...
set(CMAKE_OBJC_FLAGS "$set_objc_flags")

# Components may defined additional preprocessor definitions: use this at your
# peril, this support WILL go away! (it's here to bridge toolchain component ->
# target package switchover)
get_property(EXTRA_DEFINITIONS GLOBAL PROPERTY YOTTA_GLOBAL_DEFINITIONS)
#raw
add_definitions($${EXTRA_DEFINITIONS})
#end raw


# !!! FIXME: maybe the target can just add these to the toolchain, no need
# for repetition in every single cmake list
# Build targets may define additional preprocessor definitions for all
# components to use (such as chip variant information)
add_definitions($yotta_target_definitions)

# Provide the version of the component being built, in case components want to
# embed this into compiled libraries
set(YOTTA_COMPONENT_VERSION "$component_version")

# recurse into subdirectories for this component, using the two-argument
# add_subdirectory because the directories referred to here exist in the source
# tree, not the working directory
$add_own_subdirs

'''

Subdir_CMakeLists_Template = '''
\# NOTE: This file is generated by yotta: changes will be overwritten!

cmake_minimum_required(VERSION 2.8)

include_directories("$source_directory")

#if $executable
add_executable($object_name
    #echo '    ' + '\\n    '.join('"'+x+'"' for x in $file_names) + '\\n'
)
#else
add_library($object_name
    #echo '    ' + '\\n    '.join('"'+x+'"' for x in $file_names) + '\\n'
)
#end if

#if 'objc' in $languages
\# no proper CMake support for objective-c flags :(
set_target_properties($object_name PROPERTIES
    COMPILE_FLAGS "\${CMAKE_OBJC_FLAGS}"
)
#end if

target_link_libraries($object_name
    #echo '    ' + '\\n    '.join($link_dependencies) + '\\n'
)

'''

#this is a Cheetah template
Test_CMakeLists_Template = '''
\# NOTE: This file is generated by yotta: changes will be overwritten!

enable_testing()

include_directories("$source_directory")

#for $file_names, $object_name, $languages in $tests
add_executable($object_name
    #echo '    ' + '\\n    '.join('"'+x+'"' for x in $file_names) + '\\n'
)
#if 'objc' in $languages
\# no proper CMake support for objective-c flags :(
set_target_properties($object_name PROPERTIES
    COMPILE_FLAGS "\${CMAKE_OBJC_FLAGS}"
)
#end if
target_link_libraries($object_name
    #echo '    ' + '\\n    '.join($link_dependencies) + '\\n'
)
add_test($object_name $object_name)

#end for
'''


logger = logging.getLogger('cmakegen')

Ignore_Subdirs = set(('build','yotta_modules', 'yotta_targets', 'CMake'))


class SourceFile(object):
    def __init__(self, fname, lang):
        super(SourceFile, self).__init__()
        self.fname = fname
        self.lang = lang
    def __repr__(self):
        return self.fname
    def lang(self):
        return self.lang

class CMakeGen(object):
    def __init__(self, directory, target):
        super(CMakeGen, self).__init__()
        self.buildroot = directory
        logger.info("generate for target: %s" % target)
        self.target = target

    def generateRecursive(self, component, all_components, builddir=None, processed_components=None):
        ''' generate top-level CMakeLists for this component and its
            dependencies: the CMakeLists are all generated in self.buildroot,
            which MUST be out-of-source

            !!! NOTE: experimenting with a slightly different way of doing
            things here, this function is a generator that yields any errors
            produced, so the correct use is:

            for error in gen.generateRecursive(...):
                print error
        '''
        if builddir is None:
            builddir = self.buildroot
        if processed_components is None:
            processed_components = dict()
        if not self.target:
            yield 'Target "%s" is not a valid build target' % self.target

        toplevel = not len(processed_components)
    
        logger.debug('generate build files: %s (target=%s)' % (component, self.target))
        # because of the way c-family language includes work we need to put the
        # public header directories of all components that this component
        # depends on (directly OR indirectly) into the search path, which means
        # we need to first enumerate all the direct and indirect dependencies
        recursive_deps = component.getDependenciesRecursive(
            available_components = all_components,
                          target = self.target,
                  available_only = True
        )
        dependencies = component.getDependencies(
                  all_components,
                          target = self.target,
                  available_only = True
        )

        for name, dep in dependencies.items():
            if not dep:
                yield 'Required dependency "%s" of "%s" is not installed.' % (name, component)
        # ensure this component is assumed to have been installed before we
        # check for its dependencies, in case it has a circular dependency on
        # itself
        processed_components[component.getName()] = component
        new_dependencies = {name:c for name,c in dependencies.items() if c and not name in processed_components}
        self.generate(builddir, component, new_dependencies, dependencies, recursive_deps, toplevel)

        logger.debug('recursive deps of %s:' % component)
        for d in recursive_deps.values():
            logger.debug('    %s' % d)

        processed_components.update(new_dependencies)
        for name, c in new_dependencies.items():
            for error in self.generateRecursive(c, all_components, os.path.join(builddir, name), processed_components):
                yield error

    def checkStandardSourceDir(self, dirname, component):
        err = validate.sourceDirValidationError(dirname, component.getName())
        if err:
            logger.warn(err)

    def generate(self, builddir, component, active_dependencies, immediate_dependencies, all_dependencies, toplevel):
        ''' active_dependencies is the dictionary of components that need to be
            built for this component, but will not already have been built for
            another component.
        '''

        include_own_dir = string.Template(
            'include_directories("$path")\n'
        ).substitute(path=component.path)

        include_root_dirs = ''
        include_sys_dirs = ''
        include_other_dirs = ''
        objc_flags_set = {}
        objc_flags = []
        for name, c in all_dependencies.items():
            include_root_dirs += string.Template(
                'include_directories("$path")\n'
            ).substitute(path=c.path)
            dep_sys_include_dirs = c.getExtraSysIncludes()
            for d in dep_sys_include_dirs:
                include_sys_dirs += string.Template(
                    'include_directories(SYSTEM "$path")\n'
                ).substitute(path=os.path.join(c.path, d))
            dep_extra_include_dirs = c.getExtraIncludes()
            for d in dep_extra_include_dirs:
                include_other_dirs += string.Template(
                    'include_directories("$path")\n'
                ).substitute(path=os.path.join(c.path, d))
        for name, c in all_dependencies.items() + [(component.getName(), component)]:
            dep_extra_objc_flags = c.getExtraObjcFlags()
            # Try to warn Geraint when flags are clobbered. This will probably
            # miss some obscure flag forms, but it tries pretty hard
            for f in dep_extra_objc_flags:
                flag_name = None
                if len(f.split('=')) == 2:
                    flag_name = f.split('=')[0]
                elif f.startswith('-fno-'):
                    flag_name = f[5:]
                elif f.startswith('-fno'):
                    flag_name = f[4:]
                elif f.startswith('-f'):
                    flag_name = f[2:]
                if flag_name is not None:
                    if flag_name in objc_flags_set and objc_flags_set[flag_name] != name:
                        logger.warning(
                            'component %s Objective-C flag "%s" clobbers a value earlier set by component %s' % (
                            name, f, objc_flags_set[flag_name]
                        ))
                    objc_flags_set[flag_name] = name
                objc_flags.append(f)
        set_objc_flags = ' '.join(objc_flags)

        add_depend_subdirs = ''
        for name, c in active_dependencies.items():
            add_depend_subdirs += string.Template(
                'add_subdirectory("$working_dir/$component_name")\n'
            ).substitute(
                working_dir=builddir,
                component_name=name
            )
        
        binary_subdirs = {os.path.normpath(x) : y for x,y in component.getBinaries().items()};
        manual_subdirs = []
        autogen_subdirs = []
        for f in os.listdir(component.path):
            if f in Ignore_Subdirs or f.startswith('.') or f.startswith('_'):
                continue
            if os.path.isfile(os.path.join(component.path, f, 'CMakeLists.txt')):
                self.checkStandardSourceDir(f, component)
                # if the subdirectory has a CMakeLists.txt in it, then use that
                manual_subdirs.append(f)
            elif f in ('source', 'test') or os.path.normpath(f) in binary_subdirs:
                # otherwise, if the directory has source files, generate a
                # CMakeLists in the corresponding temporary directory, and add
                # that.
                # For now we only do this for the source and test directories -
                # in theory we could do others
                sources = self.containsSourceFiles(os.path.join(component.path, f))
                if sources:
                    autogen_subdirs.append((f, sources))
            elif f.lower() in ('source', 'src', 'test'):
                self.checkStandardSourceDir(f, component)

        add_own_subdirs = ''
        for f in manual_subdirs:
            if os.path.isfile(os.path.join(component.path, f, 'CMakeLists.txt')):
                add_own_subdirs += string.Template(
                    '''add_subdirectory(
    "$component_source_dir/$subdir_name"
    "$working_dir/$subdir_name"
)
'''
                ).substitute(
                    component_source_dir = component.path,
                             working_dir = builddir,
                             subdir_name = f
                )

        # names of all directories at this level with stuff in: used to figure
        # out what to link automatically
        all_subdirs = manual_subdirs + [x[0] for x in autogen_subdirs]
        for f, source_files in autogen_subdirs:
            if f in binary_subdirs:
                exe_name = binary_subdirs[f]
            else:
                exe_name = None
            self.generateSubDirList(builddir, f, source_files, component, all_subdirs, immediate_dependencies, exe_name);
            add_own_subdirs += string.Template(
                '''add_subdirectory(
    "$working_dir/$subdir_name"
    "$working_dir/$subdir_name"
)
'''
            ).substitute(
                working_dir = builddir,
                subdir_name = f
            )

        
        def sanitizeTarget(t):
            return t.replace('-', '_').upper()

        target_definitions = '-DTARGET=' + sanitizeTarget(self.target.getName())  + ' '
        set_targets_like = 'set(TARGET_LIKE_' + sanitizeTarget(self.target.getName()) + ' TRUE)\n'
        for target in self.target.dependencyResolutionOrder():
            if '*' not in target:
                target_definitions += '-DTARGET_LIKE_' + sanitizeTarget(target) + ' '
                set_targets_like += 'set(TARGET_LIKE_' + sanitizeTarget(target) + ' TRUE)\n'


        file_contents = str(Cheetah.Template.Template(CMakeLists_Template, searchList=[{
                            "toplevel": toplevel,
                         "target_name": self.target.getName(),
                    "set_targets_like": set_targets_like,
                      "toolchain_file": self.target.getToolchainFile(),
                      "component_name": component.getName(),
                     "include_own_dir": include_own_dir,
                   "include_root_dirs": include_root_dirs,
                    "include_sys_dirs": include_sys_dirs,
                  "include_other_dirs": include_other_dirs,
                      "set_objc_flags": set_objc_flags,
                  "add_depend_subdirs": add_depend_subdirs,
                     "add_own_subdirs": add_own_subdirs,
            "yotta_target_definitions": target_definitions,
                   "component_version": component.getVersion()
        }]))
        fsutils.mkDirP(builddir)
        fname = os.path.join(builddir, 'CMakeLists.txt')
        self.writeIfDifferent(fname, file_contents)

    def writeIfDifferent(self, fname, contents):
        try:
            with open(fname, "r+") as f:
                current_contents = f.read()
                if current_contents != contents: 
                    f.seek(0)
                    f.write(contents)
                    f.truncate()
        except IOError:
            with open(fname, "w") as f:
                f.write(contents)


    def generateSubDirList(self, builddir, dirname, source_files, component, all_subdirs, immediate_dependencies, executable_name):
        logger.debug('generate CMakeLists.txt for directory: %s' % os.path.join(component.path, dirname))

        link_dependencies = [x for x in immediate_dependencies]
        fname = os.path.join(builddir, dirname, 'CMakeLists.txt')          

        # if the directory name is 'test' then, then generate multiple
        # independent executable targets:
        if dirname == 'test':
            tests = []
            for f in source_files:
                object_name = component.getName() + '-' + os.path.basename(os.path.splitext(str(f))[0]).lower()
                tests.append([[str(f)], object_name, [f.lang]])

            # link tests against the main executable
            link_dependencies.append(component.getName())
            file_contents = str(Cheetah.Template.Template(Test_CMakeLists_Template, searchList=[{
                 'source_directory':os.path.join(component.path, dirname),
                            'tests':tests,
                'link_dependencies':link_dependencies
            }]))
        elif dirname == 'source' or executable_name:
            if executable_name:
                object_name = executable_name
                executable  = True
            else:
                object_name = component.getName()
                executable  = False
            # if we're building the main library, or an executable for this
            # component, then we should link against all the other directories
            # containing cmakelists:
            link_dependencies += [x for x in all_subdirs if x not in ('source', 'test', dirname)]
            
            file_contents = str(Cheetah.Template.Template(Subdir_CMakeLists_Template, searchList=[{
                    'source_directory':os.path.join(component.path, dirname),
                          'executable':executable,
                          'file_names':[str(f) for f in source_files],
                         'object_name':object_name,
                   'link_dependencies':link_dependencies,
                           'languages':set(f.lang for f in source_files)
            }]))
        else:
            raise Exception('auto CMakeLists for non-source/test directories is not supported')
        fsutils.mkDirP(os.path.join(builddir, dirname))
        self.writeIfDifferent(fname, file_contents);


    def containsSourceFiles(self, directory):
        c_exts    = set(('.c',))
        cpp_exts  = set(('.cpp','.cc','.cxx'))
        objc_exts = set(('.m', '.mm'))
        
        sources = []
        for root, dires, files in os.walk(directory):
            for f in files:
                name, ext = os.path.splitext(f)
                ext = ext.lower()
                if ext in c_exts:
                    sources.append(SourceFile(os.path.join(root, f), 'c'))
                elif ext in cpp_exts:
                    sources.append(SourceFile(os.path.join(root, f), 'cpp'))
                elif ext in objc_exts:
                    sources.append(SourceFile(os.path.join(root, f), 'objc'))
        return sources
