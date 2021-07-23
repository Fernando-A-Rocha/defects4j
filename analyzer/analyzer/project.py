import enum
import logging
import os
import pathlib
import re
import shutil
from typing import Generator, Sequence, Union

from analyzer import model, utility

logger = logging.getLogger(__name__)


class BugStatus(enum.Enum):
    """Status of a checkout project's bug"""

    BUGGY = "b"
    FIXED = "f"


class Project:
    """Interface of a Defects4j Project"""

    default_backup_tests = "dev_backup"
    compatible_projects = ("Cli", "Gson", "Lang")

    def __init__(self, filepath: Union[str, os.PathLike]):
        """Create a (Defects4j compatible) Project"""
        self.filepath = pathlib.Path(filepath)

        config = self.read_defects4j_config()
        self.name = config["pid"]

        if not self.is_compatible_project():
            msg = f"Incompatible project! Use one of {self.compatible_projects}"
            logger.error(msg)
            raise ValueError(msg)

        self.bug, bug_status = re.match(r"(\d+)(\w+)", config["vid"]).groups()
        if bug_status == "b":
            self.bug_status = BugStatus.BUGGY
        elif bug_status == "f":
            self.bug_status = BugStatus.FIXED
        else:
            raise ValueError(f"Invalid bug status found in config ({bug_status})")

        properties = self.read_defects4j_build_properties()
        self.relevant_class = properties["d4j.classes.relevant"]
        self.test_dir = self.filepath / properties["d4j.dir.src.tests"]
        self.package = ".".join(self.relevant_class.split(".")[:-1])
        package_path = self.package.replace(".", "/")
        self.full_test_dir = self.test_dir / package_path
        # agreement that testclasses are in the format package.to.ClassTest
        self.test_class = str(self.relevant_class) + "Test"

        # make a backup of tests at the end of init phase
        self.backup_tests()

    @property
    def test_classes(self) -> list:
        """Retrieve the relevant test classes (as found in D4J framework dir)
        given a Project name"""

        fname = f"{self.name.lower()}_tests"
        relevant_file = model.FILES / fname / "relevant" / "tests.txt"
        if not relevant_file.is_file():
            raise FileNotFoundError(f"Missing relevant tests from {fname}: {relevant_file}")

        tests = open(relevant_file).read().split()
        return tests

    def is_compatible_project(self):
        return self.name in self.compatible_projects

    def __repr__(self):
        return f"{self.name} {self.bug}{self.bug_status.value} [fp: {self.filepath}]"

    def read_defects4j_build_properties(self) -> dict:
        """Read defects4j.build.properties as key-value dictionary"""
        return utility.read_config(self.filepath / "defects4j.build.properties")

    def read_defects4j_config(self) -> dict:
        """Read .defects4j.config as key-value dictionary"""
        return utility.read_config(self.filepath / ".defects4j.config")

    def backup_tests(self, name=default_backup_tests):
        """Backup original/dev tests"""
        src = os.fspath(self.test_dir)
        dst = self.test_dir.with_name(name)
        if dst.is_dir():
            logger.debug(f"Backup already made to {dst}")
        else:
            dst = os.fspath(dst)
            shutil.move(src, dst)
            logger.debug(f"Backupped tests to {dst}")

    def restore_tests(self, name=default_backup_tests):
        """Restore original/dev tests"""
        src = os.fspath(self.test_dir.with_name(name))
        dst = os.fspath(self.test_dir)
        shutil.move(src, dst)
        logger.info(f"Restored tests to {dst}")

    def _set_dir_testsuite(self, dirpath: Union[str, os.PathLike], **kwargs):
        """Set a directory of java files as the project testsuite.
        If 'group' is specified, then only that students group
        testsuite will be used."""

        shutil.rmtree(self.test_dir, ignore_errors=True)

        no_groups = kwargs.get("no_groups", False)
        logger.debug(f"Skip students' groups? {no_groups}")
        if not no_groups:
            students = kwargs.get("group")
            logger.debug(f"Student group to restore: {students}")

            # if group argument is missing, take all compatible groups
            if students is None:
                src = os.fspath(dirpath)
            else:
                group_pattern = re.compile(r"[a-z]+_[a-z]+_([a-z0-9]+).*", re.I)
                students = students.upper()
                all_found = [
                    group_pattern.match(element.name).group(1)
                    for element in pathlib.Path(dirpath).glob("*")
                ]
                logger.debug(f"Searching {students} in Java files")
                fnames = list(pathlib.Path(dirpath).glob(f"*{students}*"))
                logger.debug(f"Found {fnames}")
                assert (
                    len(fnames) > 0
                ), f"No match found for {students}. Found {all_found}"
                assert len(fnames) == 1, f"More than one match found for {students}"
                fname = fnames[0]
                src = os.fspath(fname.resolve())

            dst = os.fspath(self.full_test_dir)
            shutil.rmtree(self.test_dir, ignore_errors=True)

            logger.debug(f"Source is {src}")
            logger.debug(f"Destination is {dst}")

            if students is None:
                shutil.copytree(src, dst)
            else:
                os.makedirs(dst)
                shutil.copy(src, dst)

        with_dev = kwargs.get("with_dev", False)
        logger.debug(f"Restore dev tests? {with_dev}")

        with_single_dev = kwargs.get("with_single_dev", False)
        logger.debug(f"Restore only one relevant dev test? {with_single_dev}")

        with_relevant_dev = kwargs.get("with_relevant_dev", False)
        logger.debug(f"Restore all relevant dev tests? {with_relevant_dev}")

        if with_dev:
            dev_test = self.test_dir.parent / self.default_backup_tests
            logger.debug(f"Dev test: {dev_test}")
            if dev_test.exists():
                shutil.copytree(dev_test, self.test_dir, dirs_exist_ok=True)
                logger.info(f"Dev tests copied into {dst}")
            else:
                msg = "Dev tests doesn't exist! Did you run 'analyzer.py backup <path>' before?"
                logger.error(msg)
        elif with_single_dev:
            src = (
                self.test_dir.parent
                / self.default_backup_tests
                / self.test_class.replace(".", "/")
            )
            src = src.with_suffix(".java")
            logger.debug(f"src is {src.resolve()}")

            dst = self.full_test_dir
            logger.debug(f"dst is {dst.resolve()}")

            os.makedirs(dst, exist_ok=True)
            shutil.copy(src, dst)
        elif with_relevant_dev:
            # set the destination path
            dst = self.test_dir
            os.makedirs(dst, exist_ok=True)
            logger.debug(f"dst is {dst.resolve()}")

            # get the list of relevant testsuites
            for i, test in enumerate(self.test_classes):
                testfile = test.replace(".", "/") + ".java"
                src_path = self.test_dir.parent / self.default_backup_tests / testfile
                dst_path = (pathlib.Path(dst) / testfile).parent

                logger.debug(f"src no {i} is {src_path.name}")
                os.makedirs(dst_path, exist_ok=True)
                shutil.copy(os.fspath(src_path), os.fspath(dst_path))

    def project_tests_root(self):
        """Get the root of project tests, based on project name"""
        tests = dict(
            Cli="cli_tests",
            Gson="gson_tests",
            Lang="lang_tests",
        )
        return model.FILES / tests[self.name]

    def set_dummy_testsuite(self):
        """Set dummy as project testsuite"""
        root = self.project_tests_root()
        return self._set_dir_testsuite(root / "dummy")

    def set_tool_testsuite(self, tool: model.Tool, **kwargs):
        """Set <tool_name> as project testsuite"""
        root = self.project_tests_root()
        return self._set_dir_testsuite(root / tool.name, **kwargs)

    def get_student_names(self, tool: model.Tool) -> Generator:
        """Get students' names from formatted java-filename"""
        root = self.project_tests_root()
        tool_dir = root / tool.name
        logger.debug(f"Parsing java files from {tool_dir}")

        pattern = re.compile(r"^([a-zA-Z]+)_([a-zA-Z]+)_([a-zA-Z]\d+)")
        for file in tool_dir.glob("*.java"):
            match = pattern.match(file.name)
            if not match:
                logger.warning(f"Invalid filename found: {file.name}")
                continue
            else:
                logger.debug(f"Match found: {match.groups()}")
                yield match.group(3)

    def get_tests(self) -> Sequence[str]:
        """Return a list of tests in current testsuite with Java format,
        i.e. package.to.TestClass"""

        # get old cwd
        cwd = pathlib.Path().resolve()

        # change dir to test dir root
        os.chdir(self.test_dir)

        # get tests as glob of all java files here (rglob recursive)
        tests = list(pathlib.Path().rglob("*.java"))
        logger.debug(f"Found {len(tests)} tests inside {self.test_dir}")

        # convert from package/to/Test.java to package.to.Test
        tests = [str(test).replace("/", ".").replace(".java", "") for test in tests]

        # change dir to old cwd
        os.chdir(cwd)

        return tests

    def _execute_defects4j_cmd(self, command: str, *args, **kwargs):
        """Execute Defects4j command in the right folder"""
        return utility.defects4j_cmd_dirpath(self.filepath, command, *args, **kwargs)

    def d4j_test(self):
        """Execute defects4j test"""
        return self._execute_defects4j_cmd("test")

    def clean(self):
        """Remove compiled files (both src and tests)"""
        target = self.filepath / "target"
        if target.exists():
            shutil.rmtree(os.fspath(target))
            logger.debug("Cleaned project")
        else:
            logger.debug("Project was already clean")

    def d4j_compile(self, **kwargs):
        """Execute defects4j compile"""
        return self._execute_defects4j_cmd("compile", **kwargs)

    def d4j_coverage(self, **kwargs):
        """Execute defects4j coverage"""
        return self._execute_defects4j_cmd("coverage", **kwargs)

    def _get_tools(self, tools: Union[model.Tool, Sequence[model.Tool]] = None):
        # if None, take every tool
        if tools is None:
            tools = [
                model.Judy(self.filepath),
                model.Jumble(self.filepath),
                model.Major(self.filepath),
                model.Pit(self.filepath),
            ]

        # if one tool is given, create list
        if isinstance(tools, model.Tool):
            tools = [tools]

        return tools

    def coverage(self, tools: Union[model.Tool, Sequence[model.Tool]] = None, **kwargs):
        """Execute coverage for selected tools.
        If 'tools' is None, every tool will be selected.
        """
        tools = self._get_tools(tools)
        logger.info(f"Executing coverage on tools {tools}")

        if len(tools) > 1 and kwargs.get("group"):
            msg = (
                "Cannot select a students group with multiple tools, retry with a single tool. "
                "Arg will be ignored!"
            )
            logger.warning(msg)
            kwargs.pop("group")

        skip_setup = kwargs.get("skip_setup", False)

        # backup original tests
        self.backup_tests()

        for tool in tools:
            logger.info(f"Start coverage of tool {tool}")

            if not skip_setup:
                # set tool tests for project
                self.clean()
                self.d4j_compile()
                self.set_tool_testsuite(tool, **kwargs)
            else:
                logger.info("Skipping setup testsuite")

            # execute defects4j coverage
            # produces coverage.xml
            self.d4j_coverage(**kwargs)

            # get student names
            students_group = kwargs.get("group")
            str_names = ""
            if students_group:
                str_names = students_group.upper()
            # else:
            #    names = list(self.get_student_names(tool))
            #    str_names = "_".join(names)

            # rename coverage.xml
            fname = "coverage.xml"
            src = self.filepath / fname
            dst = src.with_name(f"{tool.name}_{str_names}_{fname}")
            if src.exists():
                logger.debug(f"Generated {fname}")
                shutil.move(src, dst)
                logger.info(f"Generated {dst.name}")
            else:
                msg = f"Skipping {tool} because {fname} wasn't found - maybe there was an error?"
                logger.warning(msg)

    def get_mutation_scores(
        self, tools: Union[model.Tool, Sequence[model.Tool]] = None, **kwargs
    ):
        """Get mutation scores for the selected tools.
        If 'tools' is None, every tool will be selected.
        """

        tools = self._get_tools(tools)
        logger.info(f"Executing get_mutation_scores on tools {tools}")

        if not tools:
            logger.warning("Empty toolset, exit...")
            return

        # backup original tests
        self.backup_tests()

        for tool in tools:
            logger.info(f"Start get_mutation_scores for {tool}")

            # clean old target
            self.clean()

            # set entire tool testsuite
            # if student group is set, set only that testsuite
            # if with-dev is set, set also dev testsuite
            self.set_tool_testsuite(tool, **kwargs)

            # compile new testsuite
            self.d4j_compile(**kwargs)

            # must specify tests and class for replacement of dummy text
            # inside bash scripts
            if isinstance(tool, (model.Jumble, model.Pit)):
                # class under mutation name is project relevant class
                class_under_mutation = self.relevant_class

                # if I have a Jumble tool, I must specify the list of all tests
                if isinstance(tool, model.Jumble):
                    tests = " ".join(self.get_tests())
                # if I have a Pit tool, I must specify the regex of all tests
                else:
                    tests = "*Test*"

                kwargs.update(
                    {
                        "tests": tests,
                        "class": class_under_mutation,
                    }
                )

            # execute tool
            logger.debug(f"{tool} kwargs: {kwargs}")

            logger.info(f"Setupping {tool}...")
            tool.setup(**kwargs)
            logger.info("Setup completed")

            logger.info(f"Running {tool}...")
            tool.run(**kwargs)
            logger.info("Execution completed")

            logger.info("Collecting output...")
            tool.get_output()
            logger.info("Output collected")

            # get student names
            students_group = kwargs.get("group")
            str_names = ""
            if students_group:
                str_names = students_group.upper()
            # else:
            #    names = list(self.get_student_names(tool))
            #    str_names = "_".join(names)

            # get also dev, if used
            with_dev = kwargs.get("with_dev")
            if with_dev:
                str_names += "_dev"

            # create mutation score filename
            fname = f"mutation_score_{str_names}"

            # get mutation score
            score = tool.get_mutation_score(json_output=fname)

            logger.info(f"Got mutation score: {score}")

    def run_tools(
        self, tools: Union[model.Tool, Sequence[model.Tool]] = None, **kwargs
    ):
        """Run the specified tools against the current testsuite.
        If 'tools' is None, every tool will be selected.
        """
        tools = self._get_tools(tools)
        logger.info(f"Executing run_tools on tools {tools}")

        if not tools:
            logger.warning("Empty toolset, exit...")
            return

        for tool in tools:
            logger.info(f"Start run_tools for {tool}")

            # clean old target
            self.clean()

            # set entire tool testsuite
            # if student group is set, set only that testsuite
            # if with-dev is set, set also dev testsuite
            self.set_tool_testsuite(tool, **kwargs)

            # compile new testsuite
            self.d4j_compile(**kwargs)

            # must specify tests and class for replacement of dummy text
            # inside bash scripts
            if isinstance(tool, (model.Jumble, model.Pit)):
                # class under mutation name is project relevant class
                class_under_mutation = self.relevant_class

                # if I have a Jumble tool, I must specify the list of all tests
                if isinstance(tool, model.Jumble):
                    tests = " ".join(self.get_tests())
                # if I have a Pit tool, I must specify the regex of all tests
                else:
                    tests = "*Test*"

                kwargs.update(
                    {
                        "tests": tests,
                        "class": class_under_mutation,
                    }
                )

            # execute tool
            logger.debug(f"{tool} kwargs: {kwargs}")

            # get student names
            students_group = kwargs.get("group")
            str_names = ""
            if students_group:
                str_names = students_group.upper()
            # else:
            #    names = list(self.get_student_names(tool))
            #    str_names = "_".join(names)

            # get also dev, if used
            with_dev = kwargs.get("with_dev")
            with_single_dev = kwargs.get("with_single_dev")
            if with_dev:
                str_names += "_dev"
            elif with_single_dev:
                str_names += "_single_dev"

            logger.info(f"Setupping {tool}...")
            tool.setup(**kwargs)
            logger.info("Setup completed")

            logger.info(f"Running {tool}...")
            tool.run(**kwargs)
            logger.info("Execution completed")

            logger.info("Collecting output...")
            tool.get_output(str_names)
            logger.info("Output collected")

    def get_mutants(
        self, tools: Union[model.Tool, Sequence[model.Tool]] = None, **kwargs
    ):
        """Get all mutants generated by the selected tools.
        If 'tools' is None, every tool will be selected.
        """
        tools = self._get_tools(tools)
        logger.info(f"Executing get_mutants on tools {tools}")

        if not tools:
            logger.warning("Empty toolset, exit...")
            return

        # set the testsuite as dummy (empty test class)
        self.set_dummy_testsuite()

        # clean compiled and compile again
        self.clean()
        self.d4j_compile()
        logger.info("Project cleaned and compiled")

        # get dummy test name
        dummy_test_name = f"{self.name.upper()}_DUMMY_TEST"
        dummy_test = ".".join([self.package, dummy_test_name])

        # and also class under mutation name
        class_under_mutation = self.relevant_class

        # cycle over tools
        for tool in tools:
            # must specify tests and class for replacement of dummy text
            # inside bash scripts
            if isinstance(tool, (model.Jumble, model.Pit)):
                kwargs.update(
                    {
                        "tests": dummy_test,
                        "class": class_under_mutation,
                    }
                )

            logger.debug(f"{tool} kwargs: {kwargs}")

            logger.info(f"Setupping {tool}...")
            tool.setup(**kwargs)
            logger.info("Setup completed")

            logger.info(f"Running {tool}...")
            tool.run(**kwargs)
            logger.info("Execution completed")

            logger.info("Collecting output...")
            tool.get_output()
            logger.info("Output collected")
