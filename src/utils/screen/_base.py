class Screen:
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        raise NotImplementedError("Screen size retrieval not implemented for this OS.")

    @classmethod
    def get_size_str(cls) -> str:
        """
        Returns the size of the primary screen as a string "widthxheight".
        """
        width, height = cls.get_size()
        return f"{width:.0f}x{height:.0f}"

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Checks if the screen is currently locked.
        """
        raise NotImplementedError(
            "Screen lock status check not implemented for this OS."
        )
