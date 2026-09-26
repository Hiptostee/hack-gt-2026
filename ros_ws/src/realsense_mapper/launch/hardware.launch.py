"""Production hardware entry point for native Ubuntu/Raspberry Pi."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    package_share = get_package_share_directory("realsense_mapper")
    return LaunchDescription([
        DeclareLaunchArgument("enable_loop_closure", default_value="true"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(package_share, "launch", "mapping.launch.py")
            ),
            launch_arguments={
                "camera_source": "realsense",
                "enable_loop_closure": LaunchConfiguration("enable_loop_closure"),
            }.items(),
        )
    ])
