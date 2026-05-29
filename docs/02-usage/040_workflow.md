# The Project Workflow

Serena uses a project-based workflow.
A **project** is simply a directory on your filesystem that contains code and other files
that you want Serena to work with.

Assuming that you have project you want to work with (which may initially be empty),
setting up a project with Serena typically involves the following steps:

1. **Project creation**: Configuring project settings for Serena (and indexing the project, if desired)
2. **Project activation**: Making Serena aware of the project you want to work with
3. **Working on coding tasks**: Using Serena to help you with actual coding tasks in the project

(project-creation-indexing)=
## Project Creation & Indexing

Project creation is the process of defining fundamental project settings that are relevant to Serena's operation.

You can create a project either  
 * explicitly, using the project creation command (see below), or
 * implicitly, by just activating a directory as a project while already in a conversation; this will use default settings for your project (skip to the next section).

### Explicit Project Creation

To explicitly create a project, use the following command while in the project directory:

    serena project create [options] [project directory]

 * The project directory defaults to the current directory if not specified.
 * For an existing project, the programming languages will be detected based on
   the source files present, and the main language will be activated automatically.
   If multiple languages are detected, you will be prompted whether you want to enable them.  
 * For an empty project, you can optionally specify one or more languages
   to be activated explicitly via the `--language` parameter
   (e.g. `--language python --language typescript`).
 * You can optionally specify a custom project name with `--name my-name`.
 * You can immediately index the project after creation with `--index`.

(project-config)=
#### Project Configuration

After creation, you can adjust the project settings in the generated `.serena/project.yml` file
within the project directory.

The file allows you to configure ...
  * the name by which you want to refer to the project (relevant when telling the LLM to dynamically activate the project)
  * the set of programming languages for which language servers are spawned
  * the encoding used in source files
  * ignore rules
  * write access
  * [additional workspace folders](additional-workspace-folders) for cross-package reference support in monorepos
  * an `initial_prompt` passed to the agent when the project is activated
  * tool inclusion/exclusion for this project
  * and some other settings.

For detailed information on the parameters and possible settings, see the 
[template file](https://github.com/oraios/serena/blob/main/src/serena/resources/project.template.yml).

:::{note}
Many settings in project.yml *extend* or *override* settings in the global configuration file `serena_config.yml`.
So use the project configuration specifically for aspects that apply only to the particular project.
:::

**Local Overrides**. The project.yml file is intended to be versioned together with the project.
You can specify local overrides for the settings in a `project.local.yml` file in the same directory
(which, by default, is ignored by git). 
Any keys defined therein will override the respective key in `project.yml`.

(additional-workspace-folders)=
#### Additional Workspace Folders (Cross-Package References)

In monorepos or multi-package setups, Serena's language server normally only sees symbols within the
project root. To enable cross-package references (e.g. `find_referencing_symbols` discovering usages
in sibling packages), configure `additional_workspace_folders` in your `project.yml`:

```yaml
additional_workspace_folders:
  - ../shared-lib
  - ../api-client
  - /absolute/path/to/another-package
```

Paths can be absolute or relative to the project root. Each folder is registered as an LSP workspace
folder, and the language server will discover symbols and references across all listed packages.

**Currently supported for:** TypeScript. Other language servers will raise an error if this setting
is used with them. Support for additional languages can be added by implementing the
`_find_representative_source_file()` method in the respective language server class.

:::{note}
Each additional workspace folder adds startup time, as the language server needs to index the
additional projects. For large monorepos, consider listing only the packages you actively need
cross-references for.
:::

(indexing)=
### Indexing

Especially for larger projects, it can be advisable to index the project after creation, pre-caching 
symbol information provided by the language server(s). This will avoid delays during the first tool invocation
that requires symbol information.

While in the project directory, run this command:
   
    <serena> project index

Indexing has to be called only once. During regular usage, Serena will automatically update the index whenever files change.

(project-activation)=
## Project Activation
   
Project activation makes Serena aware of the project you want to work with.
You can either choose to do this
 * while in a conversation, by telling the LLM to activate a project, e.g.,
       
      * "Activate the project /path/to/my_project" (for first-time activation with auto-creation)
      * "Activate the project my_project"
   
   This requires the `activate_project` tool (enabled by default unless a project is fixed at startup).

 * when the MCP server starts, by passing the project path or name: `--project <path|name>` or `--project-from-cwd`

## Preparing Your Project

When using Serena to work on your project, it can be helpful to follow a few best practices.

### Structure Your Codebase

Serena uses the code structure for finding, reading and editing code. This means that it will
work well with well-structured code but may perform poorly on fully unstructured one (like a "God class"
with enormous, non-modular functions).

Furthermore, for languages that are not statically typed, the use of type annotations (if supported) 
are highly beneficial.

### Start from a Clean State

It is best to start a code generation task from a clean git state. Not only will
this make it easier for you to inspect the changes, but also the model itself will
have a chance of seeing what it has changed by calling `git diff` and thereby
correct itself or continue working in a followup conversation if needed.

### Use Platform-Native Line Endings

**Important**: since Serena will write to files using the system-native line endings
and it might want to look at the git diff, it is important to
set `git config core.autocrlf` to `true` on Windows.
With `git config core.autocrlf` set to `false` on Windows, you may end up with huge diffs
due to line endings only. 
It is generally a good idea to globally enable this git setting on Windows:

```shell
git config --global core.autocrlf true
```

### Logging, Linting, and Automated Tests

Serena can successfully complete tasks in an _agent loop_, where it iteratively
acquires information, performs actions, and reflects on the results.
However, Serena cannot use a debugger; it must rely on the results of program executions,
linting results, and test results to assess the correctness of its actions.
Therefore, software that is designed to meaningful interpretable outputs (e.g. log messages)
and that has a good test coverage is much easier to work with for Serena.

We generally recommend to start an editing task from a state where all linting checks and tests pass.

## Multiple Projects, Multiple Agents

There are several ways in which you might want to work with multiple projects simultaneously.

### A Single Agent Editing Multiple Projects Simultaneously

If fulfilling a task requires a single agent to edit code in multiple projects, the recommended approach is to create a **monorepo folder**,
i.e. a folder that contains all the projects as sub-folders, and open that monorepo folder as a project in Serena.
You may also use symbolic links to create a monorepo folder if the projects are located in different places on your filesystem.

If several languages are used across the projects, list all of them in `project.yml`.

### Multiple Agents Accessing a Single Serena Instance

If you want multiple agents to access the same project via a single Serena instance,
i.e. you do not want several instances of Serena (including its language servers) to be running,
you can achieve this by [starting the Serena MCP server in HTTP mode](streamable-http)
and connecting all client agents to the same HTTP endpoint.
The agents will then share the resources of the single Serena instance.

### Multiple Agents Working on Different Projects

For this use case, simply run a separate instance of Serena for each project, which naturally
occurs when Serena is started by the MCP client in stdio mode.