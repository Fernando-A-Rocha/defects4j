import abc
import io
import json
import logging
import os
import pathlib
import re
import shutil
import xml.etree.ElementTree as ET
from typing import Union

import pandas as pd

from analyzer import utility

logger = logging.getLogger(__file__)

FILES = pathlib.Path(__file__).parent / "files"


class Tool(abc.ABC):
    """Interface for mutation tools"""

    name: str = ""

    bash_script = None
    tools_output = "tools_output"
    output = []

    def __repr__(self):
        return f"{self.name.capitalize()}Tool"

    def get_output_dir(self, subdirectory: str = None):
        """Returns the output directory created inside the project directory."""
        path = self.project_dir / self.tools_output / self.name
        if subdirectory:
            path /= subdirectory
        return path

    def __init__(self, project_dir: Union[str, os.PathLike], class_under_mutation: str):
        self.project_dir = pathlib.Path(project_dir)
        self.class_under_mutation = class_under_mutation

    def _get_output_text(self, filename=None, subdirectory: str = None):
        """Return the text of a specified file inside the tool output dir.
        If omitted, defaults to the first output listed."""
        output = filename or self.output[0]
        output = self.get_output_dir(subdirectory) / output
        logger.debug(f"Reading text from {output.resolve()}")

        if not output.is_file():
            raise FileNotFoundError(output.resolve())

        with open(output) as f:
            text = f.read()
        return text

    def remove_output(self, **kwargs):
        """Utility function to remove output files"""
        for outfile in self.output:
            outfile = self.project_dir / outfile
            if outfile.is_file():
                os.remove(outfile)
            elif outfile.is_dir():
                shutil.rmtree(outfile)

    def setup(self, **kwargs):
        """Setup tool files, copying them into the project dir"""
        if self.bash_script:
            src = os.fspath(FILES / self.bash_script)
            dst = os.fspath(self.project_dir / self.bash_script)
            shutil.copy(src, dst)

        # remove output files, to not parse them as current out
        self.remove_output(**kwargs)

    def run(self, **kwargs):
        """Run the tool via its bash script"""
        script = self.project_dir / self.bash_script

        capture_out = kwargs.get("stdout", False)
        capture_err = kwargs.get("stderr", False)
        utility.bash_script(script, capture_out, capture_err)

    def _get_mutation_score(self) -> dict:
        """Returns a dict, holding killed count, live count, all count and score"""
        raise NotImplementedError

    def get_mutation_score(
        self, json_output: str = None, subdirectory: str = None
    ) -> float:
        """Get mutation score for current testsuite and tool"""
        output_dir = self.get_output_dir(subdirectory)
        logger.debug(f"Output dir is {output_dir.resolve()}")

        score_dict = self._get_mutation_score()
        logger.debug(f"Score dict is {score_dict}")

        if json_output:
            if not json_output.endswith(".json"):
                json_output += ".json"
            json_output_path = output_dir / json_output
            with open(json_output_path, "w") as f:
                json.dump(score_dict, f)
            logger.info(f"Written score json to {json_output_path}")

        return score_dict["score"]

    def get_output(self, subdirectory: str = None):
        """Get the tool output and place it under
        the specified output directory"""

        # cast output dir as pathlib object
        output_dir = self.get_output_dir(subdirectory)

        # create output directory if didn't exist
        if not output_dir.exists():
            os.makedirs(output_dir)
            logger.info(f"Created {output_dir}")

        for outfile in self.output:
            outfile = self.project_dir / outfile
            outfile = outfile.resolve()
            logger.debug(f"Working on {outfile}")
            if outfile.exists():
                src = os.fspath(outfile)
                dst = os.fspath(output_dir / outfile.name)
                shutil.move(src, dst)
                logger.info(f"Moved {outfile.name} to {output_dir}")
            else:
                msg = (
                    f"{outfile} not found.\n"
                    "If you executed run() before, then the tool got an error.\n"
                    "Try re-executing with --stdout and --stderr"
                )
                raise FileNotFoundError(msg)

    def replace(self, mapping: dict, file=None):
        """Overwrite tool flags with actual values"""
        if not file:
            file = self.bash_script
        filepath = self.project_dir / file

        # read file
        with open(filepath) as f:
            content = f.read()

        # change its content (flags)
        fixed = content
        for adict in mapping.values():
            fixed = fixed.replace(adict["original"], adict["replacement"])

        # write to file
        with open(filepath, "w") as f:
            f.write(fixed)


class Judy(Tool):
    """Judy tool"""

    name = "judy"

    bash_script = "judy.sh"
    output = ["result.json"]

    def _get_mutation_score(self) -> dict:
        text = self._get_output_text()
        result_dict = json.loads(text)

        thedict = [
            adict
            for adict in result_dict["classes"]
            if adict["name"] == self.class_under_mutation
        ]

        assert (
            len(thedict) > 0
        ), f"{self.class_under_mutation} not found in Judy output!"
        assert (
            len(thedict) == 1
        ), f"{self.class_under_mutation} appears multiple times in Judy output!"

        # take the only dict
        thedict = thedict[0]

        # and parse it
        all_count = thedict["mutantsCount"]
        killed_count = thedict["mutantsKilledCount"]
        live_count = all_count - killed_count
        score_full = killed_count / all_count
        score = round(score_full, 3)

        return dict(
            killed=killed_count,
            live=live_count,
            all=all_count,
            score=score,
            score_full=score_full,
        )


class Jumble(Tool):
    """Jumble tool"""

    name = "jumble"

    bash_script = "jumble.sh"
    verbose_bash_script = "jumble_verbose.sh"
    output = ["jumble_output.txt"]

    def _create_verbose_script(self):
        src = self.project_dir / self.bash_script
        dst = src.with_name(self.verbose_bash_script)
        shutil.copy(os.fspath(src), os.fspath(dst))
        self.replace(
            {
                "verbose": {
                    "original": 'VERBOSE=""',
                    "replacement": 'VERBOSE="--verbose"',
                }
            },
            file=self.verbose_bash_script,
        )
        logger.debug("Verbose Jumble script created")

    def setup(self, **kwargs):
        super(Jumble, self).setup()
        mutations = kwargs.get("mutations", "MUTATIONS_ALL")
        mapping = {
            "tests": {"original": "<REPLACE_TESTS>", "replacement": kwargs["tests"]},
            "class": {"original": "<REPLACE_CLASS>", "replacement": kwargs["class"]},
            "mutations": {"original": "<REPLACE_MUTATIONS>", "replacement": mutations},
        }
        self.replace(mapping=mapping)

        # create also a verbose script of Jumble that can display errors
        self._create_verbose_script()

    def _get_mutation_score(self) -> dict:
        live_mutant_pattern = re.compile(r"M FAIL:\s*([a-zA-Z.]+):(\d+):\s*(.+)")
        start_pattern = re.compile(
            r"Mutation points = \d+, unit test time limit \d+\.\d+s"
        )
        end_pattern = re.compile(r"Jumbling took \d+\.\d+s")
        error_pattern = re.compile(r"Score: \d% \(([\w ]+)")

        text = self._get_output_text()

        # try-except block to return a more understandable error
        try:
            # get indices where the mutants are defined
            i = start_pattern.search(text).end()
            j = end_pattern.search(text[i:]).start() + i
        except AttributeError:
            msg = (
                f"Cannot find start pattern. "
                f"Jumble message: {error_pattern.search(text).group(1)}"
                "\nTry running the verbose script to get "
                f"more detailed information: {self.verbose_bash_script}"
            )
            raise RuntimeError(msg) from None

        # subtract from text all the fails + get count of them
        killed_text, live_mutants_count = live_mutant_pattern.subn("", text[i:j])

        # get killed count as length of mutations with whitespaces removed
        killed_mutants_count = len(re.sub(r"\s+", "", killed_text))

        all_count = live_mutants_count + killed_mutants_count

        score_full = killed_mutants_count / all_count
        score = round(score_full, 3)

        return dict(
            killed=killed_mutants_count,
            live=live_mutants_count,
            all=all_count,
            score=score,
            score_full=score_full,
        )


class Major(Tool):
    """Major tool"""

    name = "major"

    output = ["kill.csv", "mutants.log"]

    def run(self, **kwargs):
        return utility.defects4j_cmd_dirpath(self.project_dir, "mutation", **kwargs)

    def setup(self, **kwargs):
        """Remove compiled directory that prevents
        multiple mutations, if mutations.log is missing"""
        super(Major, self).setup()

        target = self.project_dir / ".classes_mutated"
        shutil.rmtree(target, ignore_errors=True)

    def _get_mutation_score(self) -> dict:
        text = self._get_output_text("kill.csv")
        stream = io.StringIO(text)
        columns = ["MutantNo", "Status"]
        kill_df = pd.read_csv(stream, header=0, names=columns).set_index("MutantNo")

        text = self._get_output_text("mutants.log")
        stream = io.StringIO(text)
        columns = [
            "MutantNo",
            "Operator",
            "From",
            "To",
            "Signature",
            "LineNumber",
            "Description",
        ]
        mutants_df = pd.read_csv(
            stream, delimiter=":", header=None, names=columns
        ).set_index("MutantNo")

        if kill_df.empty:
            logger.info("kill.csv is empty! All mutants generated are live")
            return dict(
                killed=0,
                live=len(mutants_df),
                all=len(mutants_df),
                score=0,
                score_full=0,
            )
        else:
            df = mutants_df.join(kill_df)
            live_count = len(df[df["Status"] == "LIVE"])
            all_count = len(df)
            killed_count = all_count - live_count
            score = killed_count / all_count

            return dict(
                killed=killed_count,
                live=live_count,
                all=all_count,
                score=round(score, 3),
                score_full=score,
            )


class Pit(Tool):
    """Pit tool"""

    name = "pit"

    bash_script = "pit.sh"
    output = ["pit_report/mutations.xml"]

    def setup(self, **kwargs):
        super(Pit, self).setup()
        mapping = {
            "tests": {"original": "<TEST_REGEXP>", "replacement": kwargs["tests"]},
            "class": {"original": "<CLASS_REGEXP>", "replacement": kwargs["class"]},
        }
        self.replace(mapping=mapping)

    def _get_mutation_score(self) -> dict:
        text = self._get_output_text("mutations.xml")

        # get xml tree for xml text
        tree = ET.fromstring(text)

        # map
        bool_mapper = dict(true=True, false=False)

        killed_count = 0
        live_count = 0

        for child in tree:
            killed = bool_mapper[child.get("detected")]
            if killed:
                killed_count += 1
            else:
                live_count += 1
        all_count = killed_count + live_count
        score_full = killed_count / all_count
        score = round(score_full, 3)

        return dict(
            killed=killed_count,
            live=live_count,
            all=all_count,
            score=score,
            score_full=score_full,
        )


def get_tool(
    tool_name: str, project_dir: Union[str, os.PathLike], class_under_mutation: str
):
    """Utility function to retrieve a tool from a name and a project dir"""
    valid_tools = {
        Judy.name: Judy(project_dir, class_under_mutation),
        Jumble.name: Jumble(project_dir, class_under_mutation),
        Major.name: Major(project_dir, class_under_mutation),
        Pit.name: Pit(project_dir, class_under_mutation),
    }
    if tool_name not in valid_tools.keys():
        msg = f"Invalid tool provided: {tool_name}. Valid tools are {list(valid_tools.keys())}"
        logger.error(msg)
        raise ValueError(msg)

    return valid_tools[tool_name]


def get_all_tools(project_dir: Union[str, os.PathLike], class_under_mutation: str):
    """Utility function to retrieve all mutation tools with a project dir"""
    return [
        get_tool(
            tool_name=name,
            project_dir=project_dir,
            class_under_mutation=class_under_mutation,
        )
        for name in (Judy.name, Jumble.name, Major.name, Pit.name)
    ]
