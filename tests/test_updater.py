import subprocess

import pytest

from notetaker import updater


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", str(remote), str(dev)], check=True, capture_output=True)
    for d in (dev,):
        git(d, "config", "user.email", "t@example.com")
        git(d, "config", "user.name", "Test")
    (dev / "a.txt").write_text("1")
    git(dev, "add", ".")
    git(dev, "commit", "-m", "eerste versie")
    git(dev, "push", "-u", "origin", "main")
    user = tmp_path / "user"
    subprocess.run(["git", "clone", str(remote), str(user)], check=True, capture_output=True)
    return dev, user


def test_pending_updates_lists_new_commits(repos):
    dev, user = repos
    assert updater.is_git_checkout(user)
    assert updater.pending_updates(user) == []
    (dev / "a.txt").write_text("2")
    git(dev, "commit", "-am", "Opnamevenster verbeterd")
    git(dev, "push")
    assert updater.pending_updates(user) == ["Opnamevenster verbeterd"]
    git(user, "pull", "--ff-only")
    assert updater.pending_updates(user) == []


def test_not_a_checkout(tmp_path):
    assert updater.pending_updates(tmp_path) == []
    with pytest.raises(RuntimeError):
        updater.start_update(tmp_path)
