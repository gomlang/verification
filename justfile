[positional-arguments]
ecosystem-test *args:
    ../../goml-dev/stage2/bin/goml build
    _artifact/bin/verification "$@"

test:
    ../../goml-dev/stage2/bin/goml test
