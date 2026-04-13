default:
    @just --list

fast-test:
    @rg --files skills -g 'test_*.py' | xargs -r -n1 dirname | sort -u | xargs -r -I{} python3 -m unittest discover -s '{}' -p 'test_*.py'

test:
    @echo "All tests - Add comprehensive tests" 
