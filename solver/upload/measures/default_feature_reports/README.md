

###### (Automatically generated documentation)

# DefaultFeatureReports

## Description
Writes default_feature_reports.json and default_feature_reports.csv files used by URBANopt Scenario Default Post Processor

## Modeler Description
This measure only allows for one feature_report per simulation. If multiple features are simulated in a single simulation, a new measure must be written to disaggregate simulation results to multiple features.

## Measure Type
ReportingMeasure

## Taxonomy


## Arguments


### Feature unique identifier

**Name:** feature_id,
**Type:** String,
**Units:** ,
**Required:** false,
**Model Dependent:** false

### Feature scenario specific name

**Name:** feature_name,
**Type:** String,
**Units:** ,
**Required:** false,
**Model Dependent:** false

### URBANopt Feature Type

**Name:** feature_type,
**Type:** String,
**Units:** ,
**Required:** false,
**Model Dependent:** false

### URBANopt Feature Location

**Name:** feature_location,
**Type:** String,
**Units:** ,
**Required:** false,
**Model Dependent:** false

### Reporting Frequency
The frequency at which to report timeseries output data.
**Name:** reporting_frequency,
**Type:** Choice,
**Units:** ,
**Required:** true,
**Model Dependent:** false




