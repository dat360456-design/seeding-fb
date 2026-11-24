#!/usr/bin/env python3
"""FB Seeder — entrypoint."""
from fbseeder.gui import App


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()