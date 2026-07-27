# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""What makes a directory a case, and what to say when it is not one.

The reviewer picks a directory rather than files, so this module answers the two questions
that follow: which of its files are the volume and the detections, and — when one of them is
missing — what the reviewer is told instead of nothing happening.

The choice is deliberately *not* made in the widget. It is a function of a list of names, so
it belongs where it can be tested; the widget only opens the dialog and shows the message.
"""

from pathlib import Path

from ReformatVizLib import review

#: The volume the module reformats. NIfTI is what the detector pipeline produces.
VOLUME_PATTERN = "*.nii.gz"

#: Candidate detection files. Narrowed by :func:`review.is_review_file`, because the module
#: writes its own review into this same directory.
DETECTION_PATTERN = "*.json"


def find_files(directory):
    """Return ``(volume, detections)`` for ``directory``; either may be ``None``.

    Both are the first match in sorted order, so a directory holding more than one of either
    is opened deterministically rather than according to the filesystem's mood. Review files
    are never offered as detections.
    """
    directory = Path(directory)
    volumes = sorted(directory.glob(VOLUME_PATTERN))
    detections = [path for path in sorted(directory.glob(DETECTION_PATTERN))
                  if not review.is_review_file(path)]
    return (volumes[0] if volumes else None,
            detections[0] if detections else None)


def describe_no_volume(directory):
    """Return the message shown when the chosen directory holds no volume.

    Without it the reviewer picks a directory and nothing at all happens, which is
    indistinguishable from the module being broken.
    """
    return ("This folder has no volume, so no case has been opened. A case is a folder "
            f"holding one {VOLUME_PATTERN} volume and the detection file that goes with "
            f"it.\n\n{Path(directory).name}")


def describe_no_detections(directory):
    """Return the message shown when a volume was found but no detection file was.

    The volume is still loaded — it is worth looking at on its own — so this says what is
    missing rather than refusing, and warns that nothing is being recorded: with no detection
    file there is no review, and an aneurysm placed by hand would be lost on the next load.
    """
    return ("The volume has been loaded, but this folder has no detection file, so there are "
            "no candidates to review. Nothing is being recorded either — an aneurysm placed "
            f"by hand will not be saved.\n\n{Path(directory).name}")
