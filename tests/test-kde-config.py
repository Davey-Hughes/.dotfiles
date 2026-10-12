#!/usr/bin/env python3
"""Exercise KDE snapshots against real files, without a running desktop."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/kde-config.py"
APPLETS = "plasma-org.kde.plasma.desktop-appletsrc"


class KdeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dotfiles-kde-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / ".dotfiles"
        self.snapshots = self.repo / "os/endeavour/kde/config"
        self.legacy = self.repo / "os/endeavour/config"
        self.live = self.base / "config"
        self.state = self.base / "state"
        for directory in (self.snapshots, self.legacy, self.live):
            directory.mkdir(parents=True)
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.live),
                        XDG_STATE_HOME=str(self.state), PYTHONDONTWRITEBYTECODE="1")

    def put(self, root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_command(self, *args, success=True):
        result = subprocess.run(
            ["python3", str(SCRIPT), *args, "--repo", str(self.repo)],
            env=self.env, cwd=self.base, capture_output=True, text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_capture_previews_preferences_without_writing(self):
        original = "[General]\nBrowserApplication=old.desktop\n"
        path = self.put(self.snapshots, "kdeglobals", original)
        live = "[General]\nBrowserApplication=new.desktop\nColorSchemeHash=runtime\n"
        self.put(self.live, "kdeglobals", live)
        result = self.run_command("capture")
        self.assertIn("+BrowserApplication=new.desktop", result.stdout)
        self.assertNotIn("runtime", result.stdout)
        self.assertEqual(path.read_text(), original)
        self.assertEqual((self.live / "kdeglobals").read_text(), live)
        self.assertFalse(self.state.exists())

    def test_capture_drops_runtime_state_but_keeps_settings(self):
        self.put(self.snapshots, "kwinrc", "")
        self.put(self.snapshots, "dolphinrc", "")
        self.put(self.snapshots, APPLETS, "")
        self.put(self.snapshots, "plasmanotifyrc", "")
        self.put(self.live, "kwinrc", "[Desktops]\nId_1=local-id\nNumber=4\nRows=1\n\n"
                 "[Tiling][local-id][output-id]\npadding=4\ntiles=local-layout\n\n"
                 "[Script-krohnkite]\nwindowGap=4\n")
        self.put(self.live, "dolphinrc", "[General]\nAlwaysShowTabBar=true\n"
                 "ViewPropsTimestamp=2026,10,11\nVersion=202\n")
        self.put(self.live, "plasmanotifyrc", "[Applications][steam]\nSeen=true\n")
        self.put(self.live, APPLETS, "[Containments][2]\nactivityId=local-activity\n"
                 "lastScreen=1\nplugin=org.kde.panel\n\n[Containments][2][ConfigDialog]\n"
                 "DialogWidth=810\n\n[Containments][2][Applets][6][Configuration][General]\n"
                 "launchers=applications:obsidian.desktop\ncurrentTab=streams\n\n"
                 "[Containments][2][Wallpaper][org.kde.slideshow][General]\n"
                 "Image=file:///private/current.jpg\nSlidePaths=/private/wallpapers\n"
                 "SlideInterval=1800\n\n[ScreenMapping]\nscreenMapping=local-screen\n")
        self.run_command("capture", "--write")
        self.assertEqual((self.snapshots / "kwinrc").read_text(),
                         "[Desktops]\nNumber=4\nRows=1\n\n[Script-krohnkite]\nwindowGap=4\n")
        self.assertEqual((self.snapshots / "dolphinrc").read_text(),
                         "[General]\nAlwaysShowTabBar=true\n")
        self.assertEqual((self.snapshots / APPLETS).read_text(),
                         "[Containments][2]\nplugin=org.kde.panel\n\n"
                         "[Containments][2][Applets][6][Configuration][General]\n"
                         "launchers=applications:obsidian.desktop\n\n"
                         "[Containments][2][Wallpaper][org.kde.slideshow][General]\n"
                         "SlideInterval=1800\n")
        self.assertEqual((self.snapshots / "plasmanotifyrc").read_text(), "")

    def test_capture_is_stable_across_runtime_and_order_changes(self):
        original = "[General]\nBrowserApplication=browser.desktop\nTerminalApplication=terminal\n"
        self.put(self.snapshots, "kdeglobals", original)
        self.put(self.live, "kdeglobals", "[General]\nTerminalApplication=terminal\n"
                 "ColorSchemeHash=new\nBrowserApplication=browser.desktop\n")
        self.put(self.live, "not-selectedrc", "secret=do-not-capture\n")
        result = self.run_command("capture", "--write")
        self.assertNotIn("@@", result.stdout)
        self.assertEqual((self.snapshots / "kdeglobals").read_text(), original)
        self.assertFalse((self.snapshots / "not-selectedrc").exists())

    def test_capture_preserves_the_effective_key_and_its_options(self):
        self.put(self.snapshots, "kdeglobals", "")
        self.put(self.live, "kdeglobals", "[General]\nTerminalApplication[$e]=$HOME/old\n"
                 "TerminalApplication=/usr/bin/new\nBrowserApplication=old.desktop\n"
                 "BrowserApplication[$e]=$HOME/browser.desktop\n")
        self.run_command("capture", "--write")
        self.assertEqual((self.snapshots / "kdeglobals").read_text(),
                         "[General]\nBrowserApplication[$e]=$HOME/browser.desktop\n"
                         "TerminalApplication=/usr/bin/new\n")

    def test_apply_replaces_option_variants_of_the_same_key(self):
        self.put(self.snapshots, "kdeglobals", "[General]\nTerminalApplication=/usr/bin/new\n")
        self.put(self.live, "kdeglobals", "[General]\nTerminalApplication[$e]=$HOME/old\n")
        self.run_command("apply")
        self.assertEqual((self.live / "kdeglobals").read_text(),
                         "[General]\nTerminalApplication=/usr/bin/new\n")

    def test_capture_keeps_an_immutable_key_over_later_duplicates(self):
        self.put(self.snapshots, "kdeglobals", "")
        self.put(self.live, "kdeglobals", "[General]\nBrowserApplication[$i]=keep.desktop\n"
                 "BrowserApplication=ignored.desktop\n")
        self.run_command("capture", "--write")
        self.assertEqual((self.snapshots / "kdeglobals").read_text(),
                         "[General]\nBrowserApplication[$i]=keep.desktop\n")

    def test_apply_updates_all_repeated_groups_and_keys(self):
        self.put(self.snapshots, "kdeglobals", "[General]\nBrowserApplication=new.desktop\n")
        self.put(self.live, "kdeglobals", "[General]\nBrowserApplication=first.desktop\n"
                 "BrowserApplication=second.desktop\n\n[General]\nBrowserApplication=last.desktop\n")
        self.run_command("apply")
        result = (self.live / "kdeglobals").read_text()
        self.assertNotIn("first.desktop", result)
        self.assertNotIn("second.desktop", result)
        self.assertNotIn("last.desktop", result)
        self.assertEqual(result.count("BrowserApplication=new.desktop"), 3)
        if shutil.which("kreadconfig6"):
            read = subprocess.run(["kreadconfig6", "--file", str(self.live / "kdeglobals"),
                                   "--group", "General", "--key", "BrowserApplication"],
                                  env=self.env, capture_output=True, text=True)
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(read.stdout.strip(), "new.desktop")

    def test_capture_missing_source_leaves_all_snapshots_intact(self):
        self.put(self.snapshots, "dolphinrc", "[General]\nAlwaysShowTabBar=true\n")
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        self.put(self.live, "dolphinrc", "[General]\nAlwaysShowTabBar=false\n")
        self.run_command("capture", "--write", success=False)
        self.assertIn("AlwaysShowTabBar=true", (self.snapshots / "dolphinrc").read_text())

    def test_apply_merges_preferences_preserving_local_state_and_escaping(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\nRows=1\n")
        self.put(self.snapshots, APPLETS, "[Containments][2][Applets][6][Configuration][General]\n"
                 "launchers=applications:new.desktop\n")
        self.put(self.live, "kwinrc", "# local comment\n[Desktops]\nId_1=keep-id\nNumber=2\n"
                 "\n[Tiling][keep-id][output]\ntiles={\"width\":0.5}\n")
        self.put(self.live, APPLETS, "[Containments][2][Wallpaper][org.kde.image][General]\n"
                 "Image=file:///keep/my\\simage.jpg\n\n"
                 "[Containments][2][Applets][6][Configuration][General]\n"
                 "launchers=applications:old.desktop\ncurrentTab=streams\n")
        self.run_command("apply")
        kwin = (self.live / "kwinrc").read_text()
        self.assertIn("Number=4\nRows=1", kwin)
        self.assertIn("Id_1=keep-id", kwin)
        self.assertIn('tiles={"width":0.5}', kwin)
        self.assertIn("# local comment", kwin)
        applets = (self.live / APPLETS).read_text()
        self.assertIn("launchers=applications:new.desktop", applets)
        self.assertIn("Image=file:///keep/my\\simage.jpg", applets)
        self.assertIn("currentTab=streams", applets)
        self.assertTrue(list(self.state.rglob("kwinrc")))

    def test_apply_dry_run_does_not_create_configs_or_backups(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        result = self.run_command("apply", "--dry-run")
        self.assertIn("+Number=4", result.stdout)
        self.assertFalse((self.live / "kwinrc").exists())
        self.assertFalse(self.state.exists())

    def test_install_seeds_missing_configs_and_preserves_existing_preferences(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        self.put(self.snapshots, "dolphinrc", "[General]\nAlwaysShowTabBar=true\n")
        local = "[Desktops]\nId_1=keep-id\nNumber=2\n"
        self.put(self.live, "kwinrc", local)
        self.run_command("install")
        self.assertEqual((self.live / "kwinrc").read_text(), local)
        self.assertIn("AlwaysShowTabBar=true", (self.live / "dolphinrc").read_text())
        self.assertFalse((self.live / "dolphinrc").is_symlink())
        before = (self.live / "dolphinrc").stat().st_mtime_ns
        self.run_command("install")
        self.assertEqual((self.live / "dolphinrc").stat().st_mtime_ns, before)

    def test_install_detaches_legacy_file_without_losing_runtime_values(self):
        self.put(self.snapshots, APPLETS, "[Containments][2]\nplugin=org.kde.panel\n")
        content = "[Containments][2]\nlastScreen=1\n\n[Wallpaper]\nImage=/keep/image.jpg\n"
        old = self.put(self.legacy, APPLETS, content)
        target = self.live / APPLETS
        target.symlink_to(os.path.relpath(old, self.live))
        self.run_command("install")
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(), content)
        self.assertEqual(old.read_text(), content)
        self.assertTrue(any(p.read_text() == content for p in self.state.rglob(APPLETS)))
        target.write_text("KDE writes here now\n")
        self.assertEqual(old.read_text(), content)

    def test_install_detaches_folded_directory_and_keeps_untracked_files(self):
        self.put(self.snapshots, "kdedefaults/kdeglobals", "[General]\nColorScheme=BreezeDark\n")
        old = self.put(self.legacy, "kdedefaults/kdeglobals", "[General]\nColorScheme=Local\n")
        self.put(self.legacy, "kdedefaults/local-state", "keep this too\n")
        (self.live / "kdedefaults").symlink_to(old.parent)
        self.run_command("install")
        self.assertFalse((self.live / "kdedefaults").is_symlink())
        self.assertEqual((self.live / "kdedefaults/kdeglobals").read_text(),
                         "[General]\nColorScheme=Local\n")
        self.assertEqual((self.live / "kdedefaults/local-state").read_text(), "keep this too\n")
        (self.live / "kdedefaults/kdeglobals").write_text("local write\n")
        self.assertIn("ColorScheme=Local", old.read_text())

    def test_install_handles_dangling_legacy_links(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        (self.live / "kwinrc").symlink_to(self.legacy / "kwinrc")
        result = self.run_command("install")
        self.assertFalse((self.live / "kwinrc").is_symlink())
        self.assertIn("Number=4", (self.live / "kwinrc").read_text())
        self.assertIn("missing", result.stderr)

    def test_install_detaches_state_only_files_without_tracking_them(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        old = self.put(self.legacy, "ksmserverrc", "[Session]\nclientId=keep-session\n")
        (self.live / "ksmserverrc").symlink_to(old)
        self.run_command("install")
        self.assertFalse((self.live / "ksmserverrc").is_symlink())
        self.assertEqual((self.live / "ksmserverrc").read_text(), "[Session]\nclientId=keep-session\n")
        self.run_command("capture", "--write")
        self.assertFalse((self.snapshots / "ksmserverrc").exists())

    def test_install_refuses_a_config_root_inside_the_repo(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        self.live.rmdir()
        self.live.symlink_to(self.legacy, target_is_directory=True)
        self.run_command("install", success=False)
        self.assertFalse((self.legacy / "kwinrc").exists())

    def test_empty_xdg_variables_use_home_defaults(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        self.env.update(HOME=str(self.base), XDG_CONFIG_HOME="", XDG_STATE_HOME="")
        self.run_command("install")
        self.assertTrue((self.base / ".config/kwinrc").exists())
        self.assertIn("Number=4", (self.base / ".config/kwinrc").read_text())

    def test_install_refuses_foreign_symlink_before_changing_any_files(self):
        self.put(self.snapshots, "dolphinrc", "[General]\nAlwaysShowTabBar=true\n")
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        foreign = self.put(self.base, "foreignrc", "keep foreign settings\n")
        (self.live / "kwinrc").symlink_to(foreign)
        self.run_command("install", success=False)
        self.assertTrue((self.live / "kwinrc").is_symlink())
        self.assertEqual(foreign.read_text(), "keep foreign settings\n")
        self.assertFalse((self.live / "dolphinrc").exists())

    def test_apply_refuses_to_write_through_a_repo_symlink(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        old = self.put(self.legacy, "kwinrc", "[Desktops]\nNumber=2\n")
        (self.live / "kwinrc").symlink_to(old)
        result = self.run_command("apply", success=False)
        self.assertIn("install", result.stderr)
        self.assertIn("Number=2", old.read_text())

    def test_plain_kdedefaults_package_is_copied_without_ini_parsing(self):
        self.put(self.snapshots, "kdedefaults/package", "org.kde.breezedark.desktop")
        self.run_command("install")
        self.assertEqual((self.live / "kdedefaults/package").read_text(),
                         "org.kde.breezedark.desktop")
        self.run_command("capture", "--write")

    def test_installer_migrates_kde_only_on_the_desktop_layer(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        (self.repo / "scripts").mkdir()
        shutil.copy2(SCRIPT, self.repo / "scripts/kde-config.py")
        environment = dict(self.env, HOME=str(self.base))
        shell = 'source "$1"; test_layers="$2"; os_layers() { echo "$test_layers"; }; kde_settings'
        for layer in ("arch steamdeck", "arch endeavour"):
            result = subprocess.run(["bash", "-c", shell, "test", str(ROOT / "install.sh"), layer],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.live / "kwinrc").exists(), layer == "arch endeavour")

    @unittest.skipUnless(shutil.which("stow"), "GNU Stow is required")
    def test_stow_never_relinks_legacy_kde_sources(self):
        self.put(self.snapshots, "kwinrc", "[Desktops]\nNumber=4\n")
        self.put(self.legacy, "kwinrc", "[Desktops]\nNumber=2\n")
        self.put(self.legacy, "kate/local-state", "do not fold this directory\n")
        self.put(self.legacy, "ff-route/rules.conf", "keep stowing other desktop files\n")
        shutil.copy2(ROOT / "os/endeavour/config/.stow-local-ignore", self.legacy)
        self.run_command("install")
        result = subprocess.run(["stow", "-d", str(self.legacy.parent), "-t", str(self.live), "config"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.live / "kwinrc").is_symlink())
        self.assertFalse((self.live / "kate").exists())
        self.assertTrue((self.live / "ff-route/rules.conf").is_file())


if __name__ == "__main__":
    unittest.main()
