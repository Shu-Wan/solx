#compdef solx

_solx_completion() {
    local -a root_cmds
    root_cmds=(
        'init:Write a starter config.toml.'
        'keep:Renew CSV-flagged scratch files filtered by the keep block in config.'
        'jump:Drop into a shell on the job'"'"'s compute node (= solx job jump).'
        'completions:Emit a shell completion script (bash, zsh, or fish).'
        'version:Show version and exit (alias of --version).'
        'help:Show help and exit (alias of --help).'
        'job:Manage interactive Slurm jobs on Sol (alias: jobs).'
        'config:Inspect and edit the solx config.'
    )

    local curcontext="$curcontext" state
    _arguments -C \
        '--version[Show version and exit.]' \
        '--json[Force JSON output (machine-readable).]' \
        '--help[Show this message and exit.]' \
        '1:command:->command' \
        '*::arg:->args'

    case "$state" in
        command)
            _describe -t commands 'solx command' root_cmds
            ;;
        args)
            case "${words[1]}" in
                job|jobs)
                    local -a job_cmds
                    job_cmds=(
                        'list:Print my Sol jobs.'
                        'start:Start an interactive allocation from a config template.'
                        'stop:Cancel a job (prompts unless -y).'
                        'jump:Drop into a shell on the job'"'"'s compute node.'
                        'time:Print remaining time (D-HH:MM:SS).'
                    )
                    _describe -t commands 'solx job command' job_cmds
                    ;;
                config)
                    local -a config_cmds
                    config_cmds=(
                        'show:Print the resolved config.'
                        'edit:Open the config in $EDITOR.'
                        'import-solkeep:Migrate a legacy ~/.solkeep keep-list into the config'"'"'s [keep] block.'
                    )
                    _describe -t commands 'solx config command' config_cmds
                    ;;
                completions)
                    _values 'shell' bash zsh fish
                    ;;
            esac
            ;;
    esac
}

if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    # autoload from fpath, call function directly
    _solx_completion "$@"
else
    # eval/source/. command, register function for later
    compdef _solx_completion solx
fi
