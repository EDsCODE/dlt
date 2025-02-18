import os
import posixpath

from typing import Union, Dict
from urllib.parse import urlparse

import pytest

from tenacity import retry, stop_after_attempt, wait_fixed

from dlt.common import logger
from dlt.common import json, pendulum
from dlt.common.configuration import resolve
from dlt.common.configuration.inject import with_config
from dlt.common.configuration.specs import (
    AzureCredentials,
    AzureCredentialsWithoutDefaults,
)
from dlt.common.storages import fsspec_from_config, FilesystemConfiguration
from dlt.common.storages.fsspec_filesystem import MTIME_DISPATCH, glob_files
from dlt.common.utils import custom_environ, uniq_id
from dlt.destinations import filesystem
from dlt.destinations.impl.filesystem.configuration import (
    FilesystemDestinationClientConfiguration,
)
from tests.common.storages.utils import assert_sample_files
from tests.load.utils import ALL_FILESYSTEM_DRIVERS, AWS_BUCKET
from tests.utils import preserve_environ, autouse_test_storage
from .utils import self_signed_cert
from tests.common.configuration.utils import environment


# mark all tests as essential, do not remove
pytestmark = pytest.mark.essential


@with_config(spec=FilesystemConfiguration, sections=("destination", "filesystem"))
def get_config(config: FilesystemConfiguration = None) -> FilesystemConfiguration:
    return config


def test_filesystem_configuration() -> None:
    config = FilesystemConfiguration(bucket_url="az://root")
    assert config.protocol == "az"
    # print(config.resolve_credentials_type())
    assert (
        config.resolve_credentials_type()
        == Union[AzureCredentialsWithoutDefaults, AzureCredentials]
    )
    assert dict(config) == {
        "read_only": False,
        "bucket_url": "az://root",
        "credentials": None,
        "client_kwargs": None,
        "kwargs": None,
    }


def test_filesystem_instance(with_gdrive_buckets_env: str) -> None:
    @retry(stop=stop_after_attempt(10), wait=wait_fixed(1))
    def check_file_exists():
        files = filesystem.ls(url, detail=True)
        details = next(d for d in files if d["name"] == file_url)
        assert details["size"] == 10

    def check_file_changed():
        details = filesystem.info(file_url)
        assert details["size"] == 11
        assert (MTIME_DISPATCH[config.protocol](details) - now).seconds < 160

    bucket_url = os.environ["DESTINATION__FILESYSTEM__BUCKET_URL"]
    config = get_config()
    assert bucket_url.startswith(config.protocol)
    filesystem, url = fsspec_from_config(config)
    if config.protocol != "file":
        assert bucket_url.endswith(url)
    # do a few file ops
    now = pendulum.now()
    filename = f"filesystem_common_{uniq_id()}"
    file_url = posixpath.join(url, filename)
    try:
        filesystem.pipe(file_url, b"test bytes")
        check_file_exists()
        filesystem.pipe(file_url, b"test bytes2")
        check_file_changed()
    finally:
        filesystem.rm(file_url)
        assert not filesystem.exists(file_url)
        with pytest.raises(FileNotFoundError):
            filesystem.info(file_url)


@pytest.mark.parametrize("load_content", (True, False))
def test_filesystem_dict(with_gdrive_buckets_env: str, load_content: bool) -> None:
    bucket_url = os.environ["DESTINATION__FILESYSTEM__BUCKET_URL"]
    config = get_config()
    # enable caches
    config.read_only = True
    if config.protocol in ["memory", "file"]:
        pytest.skip(f"{config.protocol} not supported in this test")
    glob_folder = "standard_source/samples"
    # may contain query string
    bucket_url_parsed = urlparse(bucket_url)
    bucket_url = bucket_url_parsed._replace(
        path=posixpath.join(bucket_url_parsed.path, glob_folder)
    ).geturl()
    filesystem, _ = fsspec_from_config(config)
    # use glob to get data
    try:
        all_file_items = list(glob_files(filesystem, bucket_url))
        assert_sample_files(all_file_items, filesystem, config, load_content)
    except NotImplementedError as ex:
        pytest.skip(f"Skipping due to {str(ex)}")


@pytest.mark.skipif("s3" not in ALL_FILESYSTEM_DRIVERS, reason="s3 destination not configured")
def test_filesystem_instance_from_s3_endpoint(environment: Dict[str, str]) -> None:
    """Test that fsspec instance is correctly configured when using endpoint URL.
    E.g. when using an S3 compatible service such as Cloudflare R2
    """
    from s3fs import S3FileSystem

    environment["DESTINATION__FILESYSTEM__BUCKET_URL"] = "s3://dummy-bucket"
    environment["CREDENTIALS__ENDPOINT_URL"] = "https://fake-s3-endpoint.example.com"
    environment["CREDENTIALS__AWS_ACCESS_KEY_ID"] = "fake-access-key"
    environment["CREDENTIALS__AWS_SECRET_ACCESS_KEY"] = "fake-secret-key"

    config = get_config()

    filesystem, bucket_name = fsspec_from_config(config)

    assert isinstance(filesystem, S3FileSystem)
    assert filesystem.endpoint_url == "https://fake-s3-endpoint.example.com"
    assert bucket_name == "dummy-bucket"
    assert filesystem.key == "fake-access-key"
    assert filesystem.secret == "fake-secret-key"


def test_filesystem_configuration_with_additional_arguments() -> None:
    config = FilesystemConfiguration(
        bucket_url="az://root",
        kwargs={"use_ssl": True},
        client_kwargs={"verify": "public.crt"},
    )
    assert dict(config) == {
        "read_only": False,
        "bucket_url": "az://root",
        "credentials": None,
        "kwargs": {"use_ssl": True},
        "client_kwargs": {"verify": "public.crt"},
    }


@pytest.mark.skipif("s3" not in ALL_FILESYSTEM_DRIVERS, reason="s3 destination not configured")
def test_kwargs_propagate_to_s3_instance(default_buckets_env: str) -> None:
    os.environ["DESTINATION__FILESYSTEM__KWARGS"] = '{"use_ssl": false}'
    os.environ["DESTINATION__FILESYSTEM__CLIENT_KWARGS"] = '{"verify": false, "foo": "bar"}'

    config = get_config()

    if config.protocol != "s3":
        pytest.skip(f"Not configured to use {config.protocol} protocol.")

    filesystem, _ = fsspec_from_config(config)

    assert hasattr(filesystem, "kwargs")
    assert hasattr(filesystem, "client_kwargs")
    assert not filesystem.use_ssl
    assert ("verify", False) in filesystem.client_kwargs.items()
    assert ("foo", "bar") in filesystem.client_kwargs.items()


@pytest.mark.skipif("s3" not in ALL_FILESYSTEM_DRIVERS, reason="s3 destination not configured")
def test_s3_wrong_client_certificate(default_buckets_env: str, self_signed_cert: str) -> None:
    """Test whether filesystem raises an SSLError when trying to establish
    a connection with the wrong client certificate."""
    os.environ["DESTINATION__FILESYSTEM__KWARGS"] = '{"use_ssl": true}'
    os.environ["DESTINATION__FILESYSTEM__CLIENT_KWARGS"] = f'{{"verify": "{self_signed_cert}"}}'

    config = get_config()

    if config.protocol != "s3":
        pytest.skip(f"Not configured to use {config.protocol} protocol.")

    filesystem, _ = fsspec_from_config(config)

    from botocore.exceptions import SSLError

    with pytest.raises(SSLError, match="SSL: CERTIFICATE_VERIFY_FAILED"):
        print(filesystem.ls("", detail=False))


def test_filesystem_destination_config_reports_unused_placeholders(mocker) -> None:
    with custom_environ({"DATASET_NAME": "BOBO"}):
        extra_placeholders = {
            "value": 1,
            "otters": "lab",
            "dlt": "labs",
            "dlthub": "platform",
            "x": "files",
        }
        logger_spy = mocker.spy(logger, "info")
        resolve.resolve_configuration(
            FilesystemDestinationClientConfiguration(
                bucket_url="file:///tmp/dirbobo",
                layout="{schema_name}/{table_name}/{otters}-x-{x}/{load_id}.{file_id}.{timestamp}.{ext}",
                extra_placeholders=extra_placeholders,  # type: ignore
            )
        )
        logger_spy.assert_called_once_with("Found unused layout placeholders: value, dlt, dlthub")


def test_filesystem_destination_passed_parameters_override_config_values() -> None:
    config_current_datetime = "2024-04-11T00:00:00Z"
    config_extra_placeholders = {"placeholder_x": "x", "placeholder_y": "y"}
    with custom_environ(
        {
            "DESTINATION__FILESYSTEM__BUCKET_URL": "file:///tmp/dirbobo",
            "DESTINATION__FILESYSTEM__CURRENT_DATETIME": config_current_datetime,
            "DESTINATION__FILESYSTEM__EXTRA_PLACEHOLDERS": json.dumps(config_extra_placeholders),
        }
    ):
        extra_placeholders = {
            "new_value": 1,
            "dlt": "labs",
            "dlthub": "platform",
        }
        now = pendulum.now()
        config_now = pendulum.parse(config_current_datetime)

        # Check with custom datetime and extra placeholders
        # both should override config values
        filesystem_destination = filesystem(
            extra_placeholders=extra_placeholders, current_datetime=now
        )
        filesystem_config = FilesystemDestinationClientConfiguration()._bind_dataset_name(
            dataset_name="dummy_dataset"
        )
        bound_config = filesystem_destination.configuration(filesystem_config)
        assert bound_config.current_datetime == now
        assert bound_config.extra_placeholders == extra_placeholders

        # Check only passing one parameter
        filesystem_destination = filesystem(extra_placeholders=extra_placeholders)
        filesystem_config = FilesystemDestinationClientConfiguration()._bind_dataset_name(
            dataset_name="dummy_dataset"
        )
        bound_config = filesystem_destination.configuration(filesystem_config)
        assert bound_config.current_datetime == config_now
        assert bound_config.extra_placeholders == extra_placeholders

        filesystem_destination = filesystem()
        filesystem_config = FilesystemDestinationClientConfiguration()._bind_dataset_name(
            dataset_name="dummy_dataset"
        )
        bound_config = filesystem_destination.configuration(filesystem_config)
        assert bound_config.current_datetime == config_now
        assert bound_config.extra_placeholders == config_extra_placeholders
