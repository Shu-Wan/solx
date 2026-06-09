# bash completion for solx
_solx_completion() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local root_cmds="init keep jump completions version help job jobs config"
    local root_opts="--version --json --help"

    local cmd=""
    local i
    for ((i = 1; i < COMP_CWORD; i++)); do
        case "${COMP_WORDS[i]}" in
            -*) continue ;;
            *) cmd="${COMP_WORDS[i]}"; break ;;
        esac
    done

    case "$cmd" in
        "")
            COMPREPLY=( $(compgen -W "$root_cmds $root_opts" -- "$cur") )
            ;;
        job|jobs)
            COMPREPLY=( $(compgen -W "list ls start stop jump time --help" -- "$cur") )
            ;;
        config)
            COMPREPLY=( $(compgen -W "show edit import-solkeep --help" -- "$cur") )
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "$cur") )
            ;;
        keep)
            COMPREPLY=( $(compgen -W "--stage --csv-dir --solkeep --jobs --yes --dry-run --verbose --help" -- "$cur") )
            ;;
        init)
            COMPREPLY=( $(compgen -W "--force --yes --help" -- "$cur") )
            ;;
        jump)
            COMPREPLY=( $(compgen -W "--quiet --help" -- "$cur") )
            ;;
        *)
            COMPREPLY=()
            ;;
    esac
    return 0
}

complete -o default -F _solx_completion solx
