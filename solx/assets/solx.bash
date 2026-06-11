# bash completion for solx
_solx() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # On a mid-word Tab, COMP_WORDS carries the whole word; complete against
    # only the part left of the cursor.
    if [[ -n "${COMP_LINE-}" ]]; then
        local left="${COMP_LINE:0:COMP_POINT}"
        while [[ -n "$cur" && "${left%"$cur"}" == "$left" ]]; do
            cur="${cur%?}"
        done
    fi

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

    # Option values. Path candidates go through mapfile (no word splitting,
    # no glob expansion — spaces and metacharacters survive) and `compopt -o
    # filenames` (where available) so readline escapes what it inserts.
    case "$prev" in
        --csv-dir)
            type compopt &> /dev/null && compopt -o filenames 2> /dev/null
            mapfile -t COMPREPLY < <(compgen -d -- "$cur")
            return
            ;;
        --stage)
            mapfile -t COMPREPLY < <(compgen -W "all pending over90 inactive" -- "$cur")
            return
            ;;
        -j|--jobs|--timeout)
            return
            ;;
    esac

    if [[ -z "$cmd" ]]; then
        if [[ "$cur" == -* ]]; then
            mapfile -t COMPREPLY < <(compgen -W "-h --help --version --json" -- "$cur")
        else
            mapfile -t COMPREPLY < <(compgen -W "init keep jump job config completions cheatsheet version help" -- "$cur")
        fi
        return
    fi

    local flags="" words=""
    case "$cmd" in
        init)
            flags="-f --force -y --yes --json -h --help"
            words=""
            ;;
        keep)
            flags="--stage --csv-dir -j --jobs -y --yes -f --force -n --dry-run -v --verbose --json -h --help"
            words=""
            ;;
        jump)
            flags="-q --quiet --json -h --help"
            words=""
            ;;
        completions)
            flags="-h --help"
            words="bash zsh fish"
            ;;
        cheatsheet)
            flags="-h --help"
            words=""
            ;;
        version)
            flags="-h --help"
            words=""
            ;;
        help)
            flags="-h --help"
            words=""
            ;;
        job|jobs)
            if [[ -z "$sub" ]]; then
                if [[ "$cur" != -* ]]; then
                    mapfile -t COMPREPLY < <(compgen -W "list start stop jump time" -- "$cur")
                    return
                fi
                flags="-h --help"
            fi
            case "$sub" in
                list) flags="--json -h --help" ;;
                start) flags="-n --dry-run --timeout -h --help" ;;
                stop) flags="-y --yes -f --force -n --dry-run --json -h --help" ;;
                jump) flags="-q --quiet --json -h --help" ;;
                time) flags="--json -h --help" ;;
            esac
            ;;
        config)
            if [[ -z "$sub" ]]; then
                if [[ "$cur" != -* ]]; then
                    mapfile -t COMPREPLY < <(compgen -W "show edit" -- "$cur")
                    return
                fi
                flags="-h --help"
            fi
            case "$sub" in
                show) flags="--json -h --help" ;;
                edit) flags="-h --help" ;;
            esac
            ;;
    esac
    if [[ "$cur" == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$flags" -- "$cur")
    elif [[ -n "$words" && -z "$sub" ]]; then
        # $words holds positional choices; offer them only until the
        # positional is filled.
        mapfile -t COMPREPLY < <(compgen -W "$words" -- "$cur")
    fi
}

complete -F _solx solx
