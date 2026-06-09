# fish completion for solx
complete -c solx -f

complete -c solx -n '__fish_use_subcommand' -l version -d 'Show version and exit.'
complete -c solx -n '__fish_use_subcommand' -l json -d 'Force JSON output (machine-readable).'
complete -c solx -n '__fish_use_subcommand' -l help -d 'Show this message and exit.'

complete -c solx -n '__fish_use_subcommand' -a init -d 'Write a starter config.toml.'
complete -c solx -n '__fish_use_subcommand' -a keep -d 'Renew CSV-flagged scratch files filtered by the keep block in config.'
complete -c solx -n '__fish_use_subcommand' -a jump -d "Drop into a shell on the job's compute node (= solx job jump)."
complete -c solx -n '__fish_use_subcommand' -a completions -d 'Emit a shell completion script (bash, zsh, or fish).'
complete -c solx -n '__fish_use_subcommand' -a version -d 'Show version and exit (alias of --version).'
complete -c solx -n '__fish_use_subcommand' -a help -d 'Show help and exit (alias of --help).'
complete -c solx -n '__fish_use_subcommand' -a job -d 'Manage interactive Slurm jobs on Sol (alias: jobs).'
complete -c solx -n '__fish_use_subcommand' -a jobs -d 'Manage interactive Slurm jobs on Sol.'
complete -c solx -n '__fish_use_subcommand' -a config -d 'Inspect and edit the solx config.'

complete -c solx -n '__fish_seen_subcommand_from job jobs' -a list -d 'Print my Sol jobs.'
complete -c solx -n '__fish_seen_subcommand_from job jobs' -a start -d 'Start an interactive allocation from a config template.'
complete -c solx -n '__fish_seen_subcommand_from job jobs' -a stop -d 'Cancel a job (prompts unless -y).'
complete -c solx -n '__fish_seen_subcommand_from job jobs' -a jump -d "Drop into a shell on the job's compute node."
complete -c solx -n '__fish_seen_subcommand_from job jobs' -a time -d 'Print remaining time (D-HH:MM:SS).'

complete -c solx -n '__fish_seen_subcommand_from config' -a show -d 'Print the resolved config.'
complete -c solx -n '__fish_seen_subcommand_from config' -a edit -d 'Open the config in $EDITOR.'
complete -c solx -n '__fish_seen_subcommand_from config' -a import-solkeep -d "Migrate a legacy ~/.solkeep keep-list into the config's [keep] block."

complete -c solx -n '__fish_seen_subcommand_from completions' -a 'bash zsh fish'
