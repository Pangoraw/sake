import argparse
import json
import os
from datetime import datetime
from pathlib import Path
import subprocess
import sys

from rich import box
from rich.console import Console
from rich.table import Table


class Experiment(object):
    def __init__(self, expe_json):
        self.id = expe_json["id"]
        date, _ = expe_json["created"].split(".")
        self.created = datetime.fromisoformat(date)
        self.params = expe_json["params"]
        self.checkpoints = expe_json["checkpoints"]

    def get_field(self, field):
        if field in self.params:
            return self.params[field]

        for checkpoint in self.checkpoints:
            if field in checkpoint["metrics"]:
                return checkpoint["metrics"][field]

        return None

    @staticmethod
    def _present(values):
        def maybe_trim(value: str):
            # TODO: trim after ":"
            MAX_LENGTH = 60
            if len(value) > MAX_LENGTH:
                split_idx = max(MAX_LENGTH-3, value.index(":"))
                value = value[:split_idx] + "..."
            return value

        NUM_VALUES = 5
        if len(values) >= NUM_VALUES:
            values = values[:NUM_VALUES] + ["..."]

        values = [maybe_trim(value) for value in values]
        return "\n".join(values)

    @staticmethod
    def _select(values, select):
        items = values.items()
        if select is None or all([name not in values.keys() for name in select]):
            return items
        
        items = filter(lambda x: x[0] in select, items)
        return items

    def get_params(self, select):
        items = self._select(self.params, select)
        values = [f"{key}: {value}" for key, value in items]
        return self._present(values)

    def get_metrics(self, select):
        if self.checkpoints is None:
            return "0 checkpoints"
        name, checkpoint = self.get_best_checkpoint()
        items = self._select(checkpoint["metrics"], select)
        metrics = sorted(items, key=lambda x: -int(x[0] == name))
        step = checkpoint["step"]
        values = [f"step {step} (best)"] + [
            f"{key}: {value}"
            for key, value in metrics
        ]
        return self._present(values)

    def get_best_checkpoint(self):
        metrics = {}
        for checkpoint in self.checkpoints:
            primary_metric = checkpoint["primary_metric"]["name"]
            primary_metric_goal = checkpoint["primary_metric"]["goal"]
            if (primary_metric, primary_metric_goal) not in metrics:
                metrics[(primary_metric, primary_metric_goal)] = 0
            metrics[(primary_metric, primary_metric_goal)] += 1

        (name, goal), _ = sorted(metrics.items(), key=lambda x: x[1])[-1]
        metric_value = self.checkpoints[0]["metrics"][name]
        checkpoint_idx = 0
        for i, checkpoint in enumerate(self.checkpoints[1:]):
            if goal == "maximize" and checkpoint["metrics"][name] > metric_value:
                metric_value = checkpoint["metrics"][name]
                checkpoint_idx = i + 1
            elif goal == "minimize" and checkpoint["metrics"][name] < metric_value:
                metric_value = checkpoint["metrics"][name]
                checkpoint_idx = i + 1
        return name, self.checkpoints[checkpoint_idx]

    @staticmethod
    def from_file(file_path):
        with open(file_path, "r") as f:
            expe_json = json.load(f)
        return Experiment(expe_json)


class KeepsakeRepository(object):
    def __init__(self):
        self.location = self._get_location()

    def get_experiments(self):
        metadata_dir = self.location / "metadata/experiments"
        experiment_files = os.listdir(metadata_dir)
        experiments = [
            Experiment.from_file(metadata_dir / file_path)
            for file_path in experiment_files
        ]
        return experiments

    @staticmethod
    def _get_location():
        with open("keepsake.yml") as f:
            lines = f.readlines()
        for line in lines:
            if line.startswith("repository:"):
                _, location, _ = line.split('"')
                assert location.startswith("file://")
                return Path(location[7:])
        raise Exception("repository not found in keepsake.yml")


def compile_filter(format):
    # TODO: < > >= <=
    # TODO: handle numbers

    if " or " in format:
        lhs_format, rhs_format = format.split(" or ")
        lhs, rhs = compile_filter(lhs_format), compile_filter(rhs_format)
        return lambda expe: lhs(expe) or rhs(expe)

    if "!=" in format:
        field, value = format.split("!=")
        field, value = field.strip(), value.strip()
        return lambda expe: str(expe.get_field(field)) != value

    if "=" in format:
        field, value = format.split("=")
        field, value = field.strip(), value.strip()
        return lambda expe: str(expe.get_field(field)) == value


def list_experiments(args):
    repo = KeepsakeRepository()
    experiments = repo.get_experiments()

    table = Table(title="Experiments", box=box.ROUNDED)
    table.add_column("id", justify="center")
    table.add_column("Created", justify="center")
    table.add_column("Params")
    table.add_column("Checkpoints")

    filters = [compile_filter(raw_filter) for raw_filter in args.filter]
    experiments = [
        expe for expe in experiments if all(filter(expe) for filter in filters)
    ]
    if args.sort is not None:
        experiments = sorted(experiments, key=lambda expe: expe.get_field(args.sort))
    else:
        experiments = sorted(experiments, key=lambda expe: expe.created)

    for expe in experiments:
        table.add_row(
            expe.id[:7],
            expe.created.strftime("%H:%M\n%D"),
            expe.get_params(args.select),
            expe.get_metrics(args.select),
        )

    console = Console()
    if len(experiments) > 5:
        with console.pager():
            console.print(table)
    else:
        console.print(table)


def show_experiment(args):
    raise NotImplementedError()


def parse_args():
    parser = argparse.ArgumentParser("sake")
    commands = parser.add_subparsers()

    ls = commands.add_parser("list", aliases=["ls"])
    ls.add_argument("-f", "--filter", default=[], action="append")
    ls.add_argument("-s", "--select", action="append")
    ls.add_argument("--sort")
    ls.set_defaults(func=list_experiments)

    show = commands.add_parser("show")
    show.add_argument("id")
    show.set_defaults(func=show_experiment)

    args = parser.parse_args()
    if getattr(args, "func", None) is None:
        parser.print_help()
        exit(1)
    return args


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
