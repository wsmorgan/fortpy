#!/apps/python/2.7.3/bin/python
import argparse

def initialize():    
    t = UnitTester(args["stagedir"], args["verbose"], args["templates"], args["fortpy"],
                   args["rerun"], debug=args["debug"], profile=args["profile"])

    if args["compiler"]:
        t.compiler = args["compiler"]
        
    t.writeall(args["codedir"])
    result = t.runall()

    for idk in result:
        totaltime = result[idk].totaltime
        if totaltime is not None:
            timestr = "{0:.4f}".format(totaltime*1000)
        else:
            timestr = "<untimed>"
        print("RESULT: {0} = {1:.2%} success in {2} ms".format(idk, result[idk].percent,
                                                               timestr))

#Create a parser so that the script can receive arguments
parser = argparse.ArgumentParser(description="Fortpy Automated Unit Testing Tool")

#Add arguments to decide which of the systems and penalties to process.
parser.add_argument("codedir", help="Specify the path to the directory of code files to run tests for.")
parser.add_argument("-stagedir", help="Sets the directory in which to stage the unit tests.", required=True)
parser.add_argument("-templates", help="Specify the path to the folder that houses the XML templates.")
parser.add_argument("-outfile", help="Specify a path to save the comparison reports to.")
parser.add_argument("-verbose", help="Sets whether the comparison output is verbose.", action="store_true")
parser.add_argument("-mode", help="Sets the strictness of the comparison.", default="default")
parser.add_argument("-fortpy", help="The path to the fortpy templates directory.")
parser.add_argument("-rerun", help="If specified, the tests are re-run with minimal compilation.",
                    action="store_true")
parser.add_argument("-compiler", help="Specify the compiler to use for the unit testing")
parser.add_argument("-pypath", help="Specify a path to add to sys.path before running the tests.")
parser.add_argument("-debug", help="Compile the executables with DEBUG=true", 
                    action="store_true")
parser.add_argument("-profile", help="Compile and link with profiling enabled. Analyze profile.",
                    action="store_true")
#Parse the args from the commandline that ran the script, call initialize
args = vars(parser.parse_args())

#We added this argument for debugging installations. That way we can make changes
#without doing a pip install each time; just put the path to the repo root in 'pypath'
if args["pypath"]:
    import sys
    sys.path.append(args["pypath"])

import fortpy
from fortpy.testing.tester import UnitTester

initialize()
