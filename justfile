goml := env_var_or_default("GOML", "../../goml-dev/stage2/bin/goml")

[positional-arguments]
ecosystem-test *args:
    "{{goml}}" build
    _artifact/bin/verification --goml "{{goml}}" "$@"

test:
    "{{goml}}" test
