cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(ReformatViz)

#-----------------------------------------------------------------------------
# Extension meta-information
#
# This is what the Extensions Manager shows. The icon and screenshot URLs are fetched by
# whoever is browsing it, and have to be re-pointed if github.com/youssefassis/
# slicer-aneurysm-review is ever renamed or moved. Nothing here is validated at build time — a
# dead URL shows up as a missing image in the Manager, not as a CMake error.
set(EXTENSION_HOMEPAGE "https://github.com/youssefassis/slicer-aneurysm-review")
set(EXTENSION_CATEGORY "Annotation")
set(EXTENSION_CONTRIBUTORS "Youssef Assis, Erwan Kerrien (Loria lab.)")
set(EXTENSION_DESCRIPTION "Review a detector's candidates — such as intracranial aneurysms on head angiography — one at a time, on a slice view that always contains the candidate's axis. Records a verdict, rejection reason, Likert score and note per candidate and any finding the detector missed, in an autosaved review file with sign-off and an audit trail.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/youssefassis/slicer-aneurysm-review/main/ReformatViz/Resources/Icons/ReformatViz.png")
set(EXTENSION_SCREENSHOTURLS "https://raw.githubusercontent.com/youssefassis/slicer-aneurysm-review/main/docs/module-panel.jpg")
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(ReformatViz)
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
