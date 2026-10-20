"""打包入口（PyInstaller / Nuitka 都从这里开始）。开发时也可以直接 python launcher.py。"""

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from photocrate.app import main
    main()
