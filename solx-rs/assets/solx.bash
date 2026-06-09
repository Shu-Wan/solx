# bash completion for solx
_solx() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # First two non-flag words decide the (sub)command context.
    local i word cmd="" sub=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        word="${COMP_WORDS[i]}"
        [[ "$word" == -* ]] && continue
        if [[ -z "$cmd" ]]; then
            cmd="$word"
        elif [[ -z "$sub" ]]; then
            sub="$word"
        fi
    done

    # Option values.
    case "$prev" in
        --csv-dir)
            COMPREPLY=($(compgen -d -- "$cur"))
            return
            ;;
        --solkeep)
            COMPREPLY=($(compgen -f -- "$cur"))
            return
            ;;
        --stage)
            COMPREPLY=($(compgen -W "all pending over90 inactive" -- "$cur"))
            return
            ;;
        -j|--jobs|--timeout)
            return
            ;;
    esac

    if [[ -z "$cmd" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "-h --help --version --json" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "init keep jump job config completions version help" -- "$cur"))
        fi
        return
    fi

    local flags="" words=""
    case "$cmd" in
        init)
            flags="-f --force -y --yes --json --help"
            words=""
            ;;
        keep)
            flags="--stage --csv-dir --solkeep -j --jobs -y --yes -f --force -n --dry-run -v --verbose --json --help"
            words=""
            ;;
        jump)
            flags="-q --quiet --json --help"
            words=""
            ;;
        completions)
            flags=" --help"
            words="bash zsh fish"
            ;;
        version)
            flags=" --help"
            words=""
            ;;
        help)
            flags=" --help"
            words=""
            ;;
        job|jobs)
            if [[ -z "$sub" && "$cur" != -* ]]; then
                COMPREPLY=($(compgen -W "list start stop jump time" -- "$cur"))
                return
            fi
            case "$sub" in
                list) flags="--json --help" ;;
                start) flags="-n --dry-run --timeout --help" ;;
                stop) flags="-y --yes -f --force -n --dry-run --json --help" ;;
                jump) flags="-q --quiet --json --help" ;;
                time) flags="--json --help" ;;
            esac
            ;;
        config)
            if [[ -z "$sub" && "$cur" != -* ]]; then
                COMPREPLY=($(compgen -W "show edit import-solkeep" -- "$cur"))
                return
            fi
            case "$sub" in
                show) flags="--json --help" ;;
                edit) flags=" --help" ;;
                import-solkeep) flags="--solkeep -f --force --json --help" ;;
            esac
            ;;
    esac
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$flags" -- "$cur"))
    elif [[ -n "$words" ]]; then
        COMPREPLY=($(compgen -W "$words" -- "$cur"))
    fi
}

complete -F _solx solx
