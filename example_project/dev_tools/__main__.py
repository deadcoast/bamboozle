if __package__:
    from .cli import main
else:
    from cli import main

raise SystemExit(main())
