#compdef solx

_solx_job() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \
        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
        '1: :->subcommand' \
        '*:: :->subargs'

    case $state in
        (subcommand)
            local -a subcommands
            subcommands=(
                'list:Print my Sol jobs.'
                'start:Start an interactive allocation from a config template.'
                'stop:Cancel a job (prompts unless -y).'
                'jump:Drop into a shell on the job'\''s compute node.'
                'time:Print remaining time (D-HH\:MM\:SS).'
            )
            _describe -t commands 'solx job command' subcommands
            ;;
        (subargs)
            case $words[1] in
                (list)
                    _arguments \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
                (start)
                    _arguments \
                        '(-n --dry-run)'{-n,--dry-run}'[Print salloc argv without submitting.]' \
                        '--timeout[Override start_timeout (e.g. "5m", "1h").]:value:' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:template:'
                    ;;
                (stop)
                    _arguments \
                        '(-y --yes -f --force)'{-y,--yes,-f,--force}'[Skip confirmation prompt.]' \
                        '(-n --dry-run)'{-n,--dry-run}'[Print scancel argv without executing.]' \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:jobid:'
                    ;;
                (jump)
                    _arguments \
                        '(-q --quiet)'{-q,--quiet}'[Suppress the nesting / most-recent heads-up.]' \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:jobid:'
                    ;;
                (time)
                    _arguments \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:jobid:'
                    ;;
            esac
            ;;
    esac
}

_solx_config() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \
        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
        '1: :->subcommand' \
        '*:: :->subargs'

    case $state in
        (subcommand)
            local -a subcommands
            subcommands=(
                'show:Print the resolved config.'
                'edit:Open the config in $EDITOR.'
            )
            _describe -t commands 'solx config command' subcommands
            ;;
        (subargs)
            case $words[1] in
                (show)
                    _arguments \
                        '--json[Emit JSON.]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
                (edit)
                    _arguments \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
            esac
            ;;
    esac
}

_solx() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \
        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
        '--version[Show version and exit.]' \
        '--json[Force JSON output (machine-readable).]' \
        '1: :->command' \
        '*:: :->args'

    case $state in
        (command)
            local -a commands
            commands=(
                'init:Write a starter config.toml.'
                'keep:Renew CSV-flagged scratch files filtered by the keep block in config.'
                'jump:Drop into a shell on the job'\''s compute node (= solx job jump).'
                'job:Manage interactive Slurm jobs on Sol (alias\: jobs).'
                'config:Inspect and edit the solx config.'
                'completions:Emit a shell completion script (bash, zsh, or fish).'
                'version:Show version and exit (alias of --version).'
                'help:Show help and exit (alias of --help).'
            )
            _describe -t commands 'solx command' commands
            ;;
        (args)
            case $words[1] in
                (init)
                    _arguments \
                        '(-f --force -y --yes)'{-f,--force,-y,--yes}'[Overwrite without prompting.]' \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
                (keep)
                    _arguments \
                        '--stage[Which warning CSVs to read.]:value:(all pending over90 inactive)' \
                        '--csv-dir[Directory holding Sol'\''s warning CSVs.]:directory:_files -/' \
                        '(-j --jobs)'{-j,--jobs}'[Parallel touch workers.]:value:' \
                        '(-y --yes -f --force)'{-y,--yes,-f,--force}'[Skip confirmation prompt.]' \
                        '(-n --dry-run)'{-n,--dry-run}'[Print plan without executing.]' \
                        '(-v --verbose)'{-v,--verbose}'[Verbose plan + progress.]' \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
                (jump)
                    _arguments \
                        '(-q --quiet)'{-q,--quiet}'[Suppress the nesting / most-recent heads-up.]' \
                        '--json[Force JSON output (machine-readable).]' \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:jobid:'
                    ;;
                (job|jobs)
                    _solx_job
                    ;;
                (config)
                    _solx_config
                    ;;
                (completions)
                    _arguments \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]' \
                        '1:shell:(bash zsh fish)'
                    ;;
                (version)
                    _arguments \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
                (help)
                    _arguments \
                        '(-h --help)'{-h,--help}'[Show this help message and exit.]'
                    ;;
            esac
            ;;
    esac
}

if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    # autoload from fpath, call function directly
    _solx "$@"
else
    # eval/source/. command, register function for later
    compdef _solx solx
fi
