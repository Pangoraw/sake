import argparse
import json
import os
from datetime import datetime
from dateutil import parser
from functools import partial
from pathlib import Path
import subprocess
import sys

from rich import box
from rich.console import Console, RenderGroup
from rich.table import Table
from rich.prompt import Confirm
from rich.panel import Panel


class Experiment(object):
    def __init__(self, expe_json):
        self.id = expe_json["id"]
        date, _ = expe_json["created"].split(".")
        self.created = datetime.fromisoformat(date)
        self.params = expe_json["params"]
        self.checkpoints = expe_json["checkpoints"]
        self.command = expe_json["command"]

    def get_field(self, field, default_val=None):
        if field in self.params:
            return self.params[field]

        if self.checkpoints is None:
            return default_val
        _, best_checkpoint = self.get_best_checkpoint()
        if field in best_checkpoint["metrics"]:
            return best_checkpoint["metrics"][field]

        for checkpoint in self.checkpoints:
            if field in checkpoint["metrics"]:
                return checkpoint["metrics"][field]

        return default_val

    @staticmethod
    def _present(values, num_values=5):
        if num_values is None:
            num_values = 5

        def maybe_trim(value: str):
            # TODO: trim after ":"
            MAX_LENGTH = 60
            if len(value) > MAX_LENGTH:
                split_idx = max(MAX_LENGTH-3, value.index(":"))
                value = value[:split_idx] + "..."
            return value

        if len(values) >= num_values:
            values = values[:num_values+1] + ["..."]

        values = [maybe_trim(value) for value in values]
        return "\n".join(values)

    @staticmethod
    def _select(values, select):
        items = values.items()
        if select is None or all([name not in values.keys() for name in select]):
            return items, False
        
        n_before = len(items)
        items = list(filter(lambda x: x[0] in select, items))
        return items, n_before != len(items)

    @staticmethod
    def _present_value(value):
        if False and isinstance(value, float):
            return f"{value:.3f}"
        return value

    def get_params(self, select=[], show_all=False):
        items, _ = self._select(self.params, select)
        values = [f"{key}: {self._present_value(value)}" for key, value in items]
        return self._present(values, num_values=10000 if show_all else None)

    def get_keys(self):
        metrics = self.get_metrics()
        return metrics.keys()

    def _get_metrics(self, select=[]):
        if self.checkpoints is None:
            return []
        name, checkpoint = self.get_best_checkpoint()
        items, selected = self._select(checkpoint["metrics"], select)
        metrics = sorted(items, key=lambda x: -int(x[0] == name))
        return metrics

    def get_metrics(self, select=[], show_all=False):
        if self.checkpoints is None:
            return "0 checkpoints"
        name, checkpoint = self.get_best_checkpoint()
        items, selected = self._select(checkpoint["metrics"], select)
        metrics = sorted(items, key=lambda x: -int(x[0] == name))
        step = checkpoint["step"]
        values = [f"step {step} (best)"] + [
            f"{key}: {self._present_value(value)}"
            for key, value in metrics
        ]
        num_values = None
        if show_all:
            num_values = 10000
        elif selected:
            num_values = len(items)
        return self._present(values, num_values=num_values)

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

    def get_best_step(self):
        _, checkpoint = self.get_best_checkpoint()
        return None if checkpoint is None else checkpoint["step"] 

    @staticmethod
    def from_file(file_path):
        with open(file_path, "r") as f:
            expe_json = json.load(f)
        return Experiment(expe_json)


class KeepsakeRepository(object):
    def __init__(self):
        self.location = self._get_location()

    def _get_experiments_files(self):
        metadata_dir = self.location / "metadata/experiments"
        experiment_files = os.listdir(metadata_dir)
        return [
            os.path.join(metadata_dir, experiment_file)
            for experiment_file in experiment_files
        ]

    def get_experiments(self):
        experiment_files = self._get_experiments_files()
        experiments = [
            Experiment.from_file(file_path)
            for file_path in experiment_files
        ]
        return experiments

    def get_experiment(self, expe_partial_id):
        experiment_files = self._get_experiments_files()
        experiment_files= list(filter(lambda f: os.path.basename(f).startswith(expe_partial_id), experiment_files))
        n_expe = len(experiment_files)
        if n_expe >= 2:
            raise Exception(f"Found {n_expe} experiments with id '{expe_partial_id}'")
        if n_expe == 0:
            raise KeyError(expe_partial_id)
        return Experiment.from_file(experiment_files[0])

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


def try_fallback(func, default_val):
    try:
        return func(default_val)
    except:
        return default_val


class Filter:
    def __init__(self, comp, field, value):
        self.comp = comp
        self.field = field
        self.value = value

    def __call__(self, expe):
        if self.field == "created":
            field = expe.created
            convert_func = partial(parser.parse, parserinfo=parser.parserinfo(dayfirst=True))
        elif self.field == "n_checkpoints":
            field = len(expe.checkpoints) if expe.checkpoints is not None else 0
            convert_func = int
        else:
            field = expe.get_field(self.field)
            convert_func = type(field)

        comp_value = try_fallback(convert_func, self.value)
        try:
            res = self.comp(field, comp_value) 
        except:
            res = False

        return res


def compile_filter(format):
    if " or " in format:
        lhs_format, rhs_format = format.split(" or ")
        lhs, rhs = compile_filter(lhs_format), compile_filter(rhs_format)
        return lambda expe: lhs(expe) or rhs(expe)

    if " in " in format:
        value, field = format.split(" in ")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: b in a, field, value)

    if "!=" in format:
        field, value = format.split("!=")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a != b, field, value)

    if "<=" in format:
        field, value = format.split("<=")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a <= b, field, value)
    
    if ">=" in format:
        field, value = format.split(">=")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a >= b, field, value)

    if "<" in format:
        field, value = format.split("<")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a < b, field, value)

    if "=" in format:
        field, value = format.split("=")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a == b, field, value)

    if ">" in format:
        field, value = format.split(">")
        field, value = field.strip(), value.strip()
        return Filter(lambda a, b: a > b, field, value)

    raise Exception(f"invalid filter format '{format}'")


def list_experiments(args):
    repo = KeepsakeRepository()
    experiments = repo.get_experiments()

    filters = [compile_filter(raw_filter) for raw_filter in args.filter]
    experiments = [
        expe for expe in experiments if all(filter(expe) for filter in filters)
    ]
    if args.sort is not None:
        experiments = sorted(experiments, key=lambda expe: expe.get_field(args.sort, 0.0))
    else:
        experiments = sorted(experiments, key=lambda expe: expe.created)

    if args.quiet:
        for experiment in experiments:
            print(experiment.id)
        return

    table = Table(title="Experiments", box=box.ROUNDED)
    table.add_column("id", justify="center")
    table.add_column("Created", justify="center")
    table.add_column("Parameters")
    table.add_column("Checkpoints")

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
    repo = KeepsakeRepository()
    expe = repo.get_experiment(args.id)

    console = Console()
    console.print(Panel(RenderGroup(
        Panel(f"python {expe.command}", box=box.SIMPLE, title="Command"),
        Panel(expe.get_params(args.select if not args.show_all else None, args.show_all), box=box.SIMPLE, title="Parameters"),
        Panel(expe.get_metrics(args.select if not args.show_all else None, args.show_all), box=box.SIMPLE, title="Checkpoint")
    ), box=box.SIMPLE, title=f"Experiment {expe.id[:7]}"))


def diff_experiments(args):
    repo = KeepsakeRepository()
    expe1 = repo.get_experiment(args.id1)
    expe2 = repo.get_experiment(args.id2)

    get_keys = lambda metrics: set(map(lambda metric: metric[0], metrics))

    console = Console()

    keys = get_keys(expe1._get_metrics()).union(get_keys(expe2._get_metrics()))

    def get_key(d, k, v=None):
        try:
            return d[k]
        except:
            return v

    params = set(expe1.params.keys()).union(set(expe2.params.keys()))

    table = Table(title="Params", box=box.ROUNDED)
    table.add_column("Parameter")
    table.add_column(expe1.id[:7])
    table.add_column(expe2.id[:7])
    for param in params:
        value1 = get_key(expe1.params, param, None) 
        value2 =  get_key(expe2.params, param, None)
        if value1 != value2:
            table.add_row(param, str(value1), str(value2))
    console.print(table)

    table = Table(title="Metrics", box=box.ROUNDED)
    table.add_column("Metric")
    table.add_column(expe1.id[:7] + f" (step {expe1.get_best_step()})")
    table.add_column(expe2.id[:7] + f" (step {expe2.get_best_step()})")
    for key in keys:
        value1, value2 = expe1.get_field(key), expe2.get_field(key)
        if value1 != value2:
            table.add_row(key, str(value1), str(value2))

    console.print(table)

def reproduce_experiment(args):
    repo = KeepsakeRepository()
    expe = repo.get_experiment(args.id)

    console = Console()

    command = f"python {expe.command}"
    console.print(f"Command for experiment [cyan]{expe.id[:7]}[/cyan] is:")
    console.print(f"\n{command}\n")

    if not args.yes:
        yes = Confirm.ask(f"Do you want to run it?")
        if not yes:
            console.print("Aborting")
            return

    os.system(command)


def parse_args():
    parser = argparse.ArgumentParser("sake")
    commands = parser.add_subparsers()

    ls = commands.add_parser("list", aliases=["ls"])
    ls.add_argument("-f", "--filter", default=[], action="append")
    ls.add_argument("-s", "--select", action="append")
    ls.add_argument("-q", "--quiet", action="store_true", help="return only the ids")
    ls.add_argument("--sort")
    ls.set_defaults(func=list_experiments)

    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("-s", "--select", action="append")
    show.add_argument("-a", "--show-all", action="store_true")
    show.set_defaults(func=show_experiment)

    diff = commands.add_parser("diff")
    diff.add_argument("id1")
    diff.add_argument("id2")
    diff.set_defaults(func=diff_experiments)

    repr_parser = commands.add_parser("repr", aliases=["reproduce"])
    repr_parser.add_argument("id")
    repr_parser.add_argument("-y", "--yes", action="store_true", 
                             help="do not ask for confirmation")
    repr_parser.set_defaults(func=reproduce_experiment)

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
