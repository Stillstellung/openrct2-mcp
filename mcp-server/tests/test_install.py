"""Unit tests for the shared post-install helpers."""

import unittest

from openrct2_mcp.install import merge_mcp_config, set_ini_value


class SetIniValueTests(unittest.TestCase):
    def test_replaces_existing_key_and_keeps_crlf(self):
        text = "[general]\r\nlanguage = en-US\r\n\r\n[plugin]\r\nenable_hot_reloading = false\r\nallowed_hosts = \"\"\r\n"
        result = set_ini_value(text, "plugin", "enable_hot_reloading", "true")
        self.assertEqual(result, text.replace("enable_hot_reloading = false", "enable_hot_reloading = true"))

    def test_same_key_in_other_section_untouched(self):
        text = "[a]\nkey = 1\n\n[b]\nkey = 2\n"
        self.assertEqual(set_ini_value(text, "b", "key", "9"), "[a]\nkey = 1\n\n[b]\nkey = 9\n")

    def test_adds_key_before_next_section(self):
        text = "[plugin]\nallowed_hosts = \"\"\n\n[sound]\nvolume = 1\n"
        self.assertEqual(
            set_ini_value(text, "plugin", "enable_hot_reloading", "true"),
            "[plugin]\nallowed_hosts = \"\"\nenable_hot_reloading = true\n\n[sound]\nvolume = 1\n",
        )

    def test_adds_key_to_last_section(self):
        text = "[general]\nx = 1\n\n[plugin]\nallowed_hosts = \"\"\n"
        self.assertEqual(
            set_ini_value(text, "plugin", "enable_hot_reloading", "true"),
            "[general]\nx = 1\n\n[plugin]\nallowed_hosts = \"\"\nenable_hot_reloading = true\n",
        )

    def test_adds_missing_section(self):
        self.assertEqual(
            set_ini_value("[general]\nx = 1\n", "plugin", "enable_hot_reloading", "true"),
            "[general]\nx = 1\n[plugin]\nenable_hot_reloading = true\n",
        )

    def test_idempotent(self):
        text = "[plugin]\nenable_hot_reloading = true\n"
        self.assertEqual(set_ini_value(text, "plugin", "enable_hot_reloading", "true"), text)


class MergeMcpConfigTests(unittest.TestCase):
    def test_keeps_other_servers(self):
        existing = {"mcpServers": {"other": {"command": "x"}}}
        merged = merge_mcp_config(existing, r"C:\repo\.venv\Scripts\python.exe")
        self.assertEqual(merged["mcpServers"]["other"], {"command": "x"})
        server = merged["mcpServers"]["openrct2"]
        self.assertEqual(server["command"], r"C:\repo\.venv\Scripts\python.exe")
        self.assertEqual(server["args"], ["-m", "openrct2_mcp"])
        self.assertEqual(existing, {"mcpServers": {"other": {"command": "x"}}})

    def test_empty_config(self):
        merged = merge_mcp_config({}, "/repo/.venv/bin/python")
        self.assertEqual(list(merged["mcpServers"]), ["openrct2"])


if __name__ == "__main__":
    unittest.main()
