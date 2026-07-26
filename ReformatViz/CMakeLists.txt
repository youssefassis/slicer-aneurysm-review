#-----------------------------------------------------------------------------
set(MODULE_NAME ReformatViz)

#-----------------------------------------------------------------------------
# ReformatVizLib is installed next to the module. Slicer puts the scripted-modules
# directory on sys.path, so `from ReformatVizLib import geometry` resolves at runtime.
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  ReformatVizLib/__init__.py
  ReformatVizLib/detections.py
  ReformatVizLib/geometry.py
  ReformatVizLib/rendering.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.png
  Resources/UI/${MODULE_NAME}.ui
  )

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)

  # Register the unittest subclass in the main script as a ctest.
  # Note that the test will also be available at runtime.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)

  # Additional build-time testing
  add_subdirectory(Testing)
endif()
