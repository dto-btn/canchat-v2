import os
import pytest
from open_webui.utils.misc import validate_path


def test_validate_path_valid_file(tmp_path):
    # Valid path inside base_dir
    base_dir = tmp_path
    valid_file = base_dir / "safe_file.txt"
    valid_file.touch()

    validated = validate_path(str(valid_file), str(base_dir))
    assert validated == str(valid_file.resolve())


def test_validate_path_traversal_attempt(tmp_path):
    # Path traversal attempt (parent directory)
    base_dir = tmp_path
    traversal_path = base_dir / "../outside.txt"

    with pytest.raises(ValueError, match="outside of expected directories"):
        validate_path(str(traversal_path), str(base_dir))


def test_validate_path_absolute_traversal(tmp_path):
    # Create a path that is strictly outside tmp_path
    base_dir = tmp_path
    result_path = base_dir.parent / "outside_abs.txt"

    with pytest.raises(ValueError, match="outside of expected directories"):
        validate_path(str(result_path), str(base_dir))


def test_validate_path_resolution_inside(tmp_path):
    # Path traversal attempt with '..' components that resolve inside
    base_dir = tmp_path / "base"
    base_dir.mkdir()

    subdir = base_dir / "subdir"
    subdir.mkdir()

    safe_trick = subdir / "../safe_trick.txt"
    (base_dir / "safe_trick.txt").touch()

    validated = validate_path(str(safe_trick), str(base_dir))
    assert validated == str((base_dir / "safe_trick.txt").resolve())


def test_validate_path_multiple_bases(tmp_path):
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    dir2 = tmp_path / "dir2"
    dir2.mkdir()

    file_in_dir2 = dir2 / "file.txt"
    file_in_dir2.touch()

    validated = validate_path(str(file_in_dir2), [str(dir1), str(dir2)])
    assert validated == str(file_in_dir2.resolve())

    with pytest.raises(ValueError, match="outside of expected directories"):
        validate_path(str(file_in_dir2), [str(dir1)])


def test_validate_path_pathlike_base_dir(tmp_path):
    base_dir = tmp_path
    valid_file = base_dir / "safe_file.txt"
    valid_file.touch()

    validated = validate_path(valid_file, base_dir)
    assert validated == str(valid_file.resolve())


def test_validate_path_pathlike_multiple_bases(tmp_path):
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    dir2 = tmp_path / "dir2"
    dir2.mkdir()

    file_in_dir2 = dir2 / "file.txt"
    file_in_dir2.touch()

    validated = validate_path(file_in_dir2, [dir1, dir2])
    assert validated == str(file_in_dir2.resolve())
