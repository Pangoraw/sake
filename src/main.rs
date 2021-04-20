#![feature(str_split_once)]

use serde::Deserialize;
use std::path::PathBuf;
use structopt::StructOpt;

#[derive(Debug)]
enum SakeError {
    IOError(std::io::Error),
    YAMLError(serde_yaml::Error),
    JSONError(serde_json::Error),
    InvalidRepository(String),
}
type Result<T> = std::result::Result<T, SakeError>;

macro_rules! from_err {
    ($fr: ty, $to: path) => {
        impl From<$fr> for SakeError {
            fn from(err: $fr) -> Self {
                $to(err)
            }
        }
    };
}

from_err!(std::io::Error, SakeError::IOError);
from_err!(serde_yaml::Error, SakeError::YAMLError);
from_err!(serde_json::Error, SakeError::JSONError);

impl std::fmt::Display for SakeError {
    fn fmt(&self, fmt: &mut std::fmt::Formatter) -> std::result::Result<(), std::fmt::Error> {
        use SakeError::*;
        let value = match self {
            IOError(err) => format!("io error: {}", err),
            YAMLError(err) => format!("yaml error: {}", err),
            JSONError(err) => format!("json error: {}", err),
            InvalidRepository(err) => format!("invalid repository: {}", err),
        };
        fmt.write_str(&value)
    }
}

#[derive(StructOpt, Debug)]
#[structopt(name = "sake", about = "A replacement for the keepsake cli")]
enum Opt {
    List {
        #[structopt(name = "filter", long)]
        filters: Vec<String>,

        #[structopt(long)]
        sort: Option<String>,

        #[structopt(long)]
        only: Vec<String>,
    },
    Show,
}

#[derive(Deserialize)]
struct ExperimentConfig {
    repository: String,
    storage: String,
}

#[derive(Deserialize)]
struct CheckpointPrimaryMetric {
    name: String,
    goal: String,
}

#[derive(Deserialize)]
struct ExperimentCheckpoint {
    id: String,
    created: String,
    metrics: serde_json::Value,
    step: usize,
    path: String,
    primary_metric: CheckpointPrimaryMetric,
}

#[derive(Deserialize)]
struct Experiment {
    id: String,
    created: String,
    params: serde_json::Value,
    host: String,
    user: String,
    config: ExperimentConfig,
    command: String,
    path: String,
    python_version: String,
    python_packages: serde_json::Value,
    checkpoints: Option<Vec<ExperimentCheckpoint>>,
}

impl Experiment {
    fn from_file(file_path: PathBuf) -> Result<Experiment> {
        let content = std::fs::read_to_string(file_path)?;

        serde_json::from_str(&content).map_err(SakeError::JSONError)
    }

    pub fn find_field(&self, field: &str) -> Option<serde_json::Value> {
        if let Some(params) = self.params.as_object() {
            if params.contains_key(field) {
                return params.get(field).cloned();
            }
        }

        if let Some(checkpoints) = &self.checkpoints {
            for checkpoint in checkpoints {
                if let Some(metrics) = checkpoint.metrics.as_object() {
                    if metrics.contains_key(field) {
                        return metrics.get(field).cloned();
                    }
                }
            }
        }

        None
    }
}

#[derive(Debug)]
struct KeepsakeRepository {
    location: PathBuf,
}

#[derive(Deserialize, Debug)]
struct KeepsakeConfig {
    repository: String,
}

#[derive(Debug)]
enum Filter {
    Equal { field: String, value: String },
}

impl Filter {
    fn from_str(value: &String) -> Result<Filter> {
        if let Some((start, finish)) = value.split_once("=") {
            Ok(Filter::Equal {
                field: start.to_string(),
                value: finish.to_string(),
            })
        } else {
            Err(SakeError::InvalidRepository(format!(
                "invalid filter: {}",
                value
            )))
        }
    }

    pub fn test(&self, expe: &Experiment) -> bool {
        match self {
            Filter::Equal { field, value } => {
                if let Some(expe_value) = expe.find_field(field) {
                    if let Some(string_val) = value_to_string(expe_value) {
                        string_val == *value
                    } else {
                        false
                    }
                } else {
                    false
                }
            }
        }
    }
}

fn value_to_string(value: serde_json::Value) -> Option<String> {
    // TODO: Number values
    match value {
        serde_json::Value::Null => Some("null".to_string()),
        serde_json::Value::Bool(true) => Some("true".to_string()),
        serde_json::Value::String(value) => Some(value),
        _ => None,
    }
}

impl KeepsakeRepository {
    fn init() -> Result<KeepsakeRepository> {
        // TODO: find file location by recursively going up
        let file_content = std::fs::read_to_string("keepsake.yml")?;
        let config: KeepsakeConfig = serde_yaml::from_str(&file_content)?;

        if let Some(location) = config.repository.strip_prefix("file://") {
            let location = PathBuf::from(location);
            Ok(KeepsakeRepository { location })
        } else {
            Err(SakeError::InvalidRepository(format!(
                "invalid repository location: {}, only file is supported",
                config.repository,
            )))
        }
    }

    pub fn list_experiments(
        &self,
        raw_filters: &Vec<String>,
        sort: Option<String>,
        only: &Vec<String>,
    ) -> Result<()> {
        let experiment_folder = self.location.join("metadata/experiments/");
        let entries = std::fs::read_dir(experiment_folder)?
            .collect::<std::result::Result<Vec<std::fs::DirEntry>, std::io::Error>>()?;

        let experiments = entries
            .iter()
            .map(|entry| Experiment::from_file(entry.path()))
            .collect::<Result<Vec<Experiment>>>()?;

        let filters = raw_filters
            .iter()
            .map(Filter::from_str)
            .collect::<Result<Vec<Filter>>>()?;

        println!("{:?}", filters);

        experiments
            .iter()
            .filter(|expe| filters.iter().all(|filter| filter.test(expe)))
            .for_each(|expe| {
                println!(
                    // TODO: --only && --sort
                    "id: {} method: {:?}",
                    expe.id.split_at(7).0,
                    expe.find_field("method")
                );
            });

        Ok(())
    }
}

fn main() {
    let opt = Opt::from_args();

    let repo = KeepsakeRepository::init().unwrap();
    let res = match opt {
        Opt::List {
            filters,
            sort,
            only,
        } => repo.list_experiments(&filters, sort, &only),
        Opt::Show => Ok(()),
    };
    match res {
        Err(err) => println!("error: {}", err),
        Ok(()) => {}
    }
}
