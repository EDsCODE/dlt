---
title: Parquet
description: The parquet file format
keywords: [parquet, file formats]
---

# Parquet File Format

[Apache Parquet](https://en.wikipedia.org/wiki/Apache_Parquet) is a free and open-source column-oriented data storage format in the Apache Hadoop ecosystem. `dlt` is capable of storing data in this format when configured to do so.

To use this format, you need a `pyarrow` package. You can get this package as a `dlt` extra as well:

```sh
pip install dlt[parquet]
```

## Supported Destinations

Supported by: **BigQuery**, **DuckDB**, **Snowflake**, **filesystem**, **Athena**, **Databricks**, **Synapse**

By setting the `loader_file_format` argument to `parquet` in the run command, the pipeline will store your data in the parquet format at the destination:

```py
info = pipeline.run(some_source(), loader_file_format="parquet")
```

## Destination AutoConfig
`dlt` uses [destination capabilities](../../walkthroughs/create-new-destination.md#3-set-the-destination-capabilities) to configure the parquet writer:
* It uses decimal and wei precision to pick the right **decimal type** and sets precision and scale.
* It uses timestamp precision to pick the right **timestamp type** resolution (seconds, micro, or nano).

## Writer settings

Under the hood, `dlt` uses the [pyarrow parquet writer](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html) to create the files. The following options can be used to change the behavior of the writer:

- `flavor`: Sanitize schema or set other compatibility options to work with various target systems. Defaults to None which is **pyarrow** default.
- `version`: Determine which Parquet logical types are available for use, whether the reduced set from the Parquet 1.x.x format or the expanded logical types added in later format versions. Defaults to "2.6".
- `data_page_size`: Set a target threshold for the approximate encoded size of data pages within a column chunk (in bytes). Defaults to None which is **pyarrow** default.
- `timestamp_timezone`: A string specifying timezone, default is UTC.
- `coerce_timestamps`: resolution to which coerce timestamps, choose from **s**, **ms**, **us**, **ns**
- `allow_truncated_timestamps` - will raise if precision is lost on truncated timestamp.

:::tip
Default parquet version used by `dlt` is 2.4. It coerces timestamps to microseconds and truncates nanoseconds silently. Such setting
provides best interoperability with database systems, including loading panda frames which have nanosecond resolution by default
:::

Read the [pyarrow parquet docs](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html) to learn more about these settings.

Example:

```toml
[normalize.data_writer]
# the default values
flavor="spark"
version="2.4"
data_page_size=1048576
timestamp_timezone="Europe/Berlin"
```

Or using environment variables:

```sh
NORMALIZE__DATA_WRITER__FLAVOR
NORMALIZE__DATA_WRITER__VERSION
NORMALIZE__DATA_WRITER__DATA_PAGE_SIZE
NORMALIZE__DATA_WRITER__TIMESTAMP_TIMEZONE
```

### Timestamps and timezones
`dlt` adds timezone (UTC adjustment) to all timestamps regardless of a precision (from seconds to nanoseconds). `dlt` will also create TZ aware timestamp columns in
the destinations. [duckdb is an exception here](../destinations/duckdb.md#supported-file-formats)

### Disable timezones / utc adjustment flags
You can generate parquet files without timezone adjustment information in two ways:
1. Set the **flavor** to spark. All timestamps will be generated via deprecated `int96` physical data type, without the logical one
2. Set the **timestamp_timezone** to empty string (ie. `DATA_WRITER__TIMESTAMP_TIMEZONE=""`) to generate logical type without UTC adjustment.

To our best knowledge, arrow will convert your timezone aware DateTime(s) to UTC and store them in parquet without timezone information.
