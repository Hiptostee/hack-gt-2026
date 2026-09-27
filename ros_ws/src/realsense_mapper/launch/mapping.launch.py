import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    realsense_share = get_package_share_directory("realsense2_camera")
    mapper_share = get_package_share_directory("realsense_mapper")

    camera_source = LaunchConfiguration("camera_source")
    camera_profile = LaunchConfiguration("camera_profile")
    enable_backpack_stack = LaunchConfiguration("enable_backpack_stack")
    backpack_model_path = LaunchConfiguration("backpack_model_path")
    enable_companion_bridge = LaunchConfiguration("enable_companion_bridge")
    enable_mapping = LaunchConfiguration("enable_mapping")
    enable_imu = LaunchConfiguration("enable_imu")
    enable_icp = LaunchConfiguration("enable_icp")
    enable_loop_closure = LaunchConfiguration("enable_loop_closure")
    odom_image_decimation = LaunchConfiguration("odom_image_decimation")
    odom_max_update_rate = LaunchConfiguration("odom_max_update_rate")
    imu_i2c_bus = LaunchConfiguration("imu_i2c_bus")
    imu_i2c_address = LaunchConfiguration("imu_i2c_address")

    # Measure these on the physical rig. Translation defaults to zero rather
    # than the old, incorrect -2 metre lever arm.
    imu_x = LaunchConfiguration("imu_x")
    imu_y = LaunchConfiguration("imu_y")
    imu_z = LaunchConfiguration("imu_z")
    imu_qx = LaunchConfiguration("imu_qx")
    imu_qy = LaunchConfiguration("imu_qy")
    imu_qz = LaunchConfiguration("imu_qz")
    imu_qw = LaunchConfiguration("imu_qw")

    use_realsense = IfCondition(PythonExpression(["'", camera_source, "' == 'realsense'"]))
    use_tcp = IfCondition(PythonExpression(["'", camera_source, "' == 'tcp'"]))

    camera_topics = [
        ("rgb/image", "/camera/color/image_raw"),
        ("depth/image", "/camera/aligned_depth_to_color/image_raw"),
        ("rgb/camera_info", "/camera/color/camera_info"),
    ]

    common = {
        "frame_id": "camera_link",
        "map_frame_id": "map",
        "wait_for_transform": 0.2,
        "qos": 2,
        "qos_camera_info": 2,
        "Vis/MinInliers": "15",
        "Vis/MaxDepth": "4.0",
        "Reg/Force3DoF": "false",
        "RGBD/ForceOdom3DoF": "false",
    }

    # One local trajectory. Visual registration is primary; when enable_icp is
    # true, ICP refines the SAME RGB-D registration instead of creating a
    # second correlated odometry stream for an EKF.
    odom_parameters = dict(common)
    odom_parameters.update({
        "odom_frame_id": "odom",
        "publish_tf": False,  # valid_visual_odom publishes only accepted TFs
        "approx_sync": False,
        "max_update_rate": ParameterValue(odom_max_update_rate, value_type=float),
        "always_process_most_recent_frame": True,
        "topic_queue_size": 2,
        "sync_queue_size": 5,
        "wait_imu_to_init": ParameterValue(enable_imu, value_type=bool),
        "always_check_imu_tf": False,
        "Odom/GuessMotion": "true",
        "Odom/ResetCountdown": "5",
        "Odom/ImageDecimation": ParameterValue(odom_image_decimation, value_type=str),
        "Reg/Strategy": ParameterValue(PythonExpression([
            "'2' if '", enable_icp, "'.lower() in ('true','1') else '0'"
        ]), value_type=str),
        "Icp/PointToPlane": "true",
        "Icp/VoxelSize": "0.05",
        "Icp/Iterations": "15",
        "Icp/MaxCorrespondenceDistance": "0.12",
        "Icp/CorrespondenceRatio": "0.15",
        "Icp/MaxTranslation": "0.25",
        "Icp/MaxRotation": "0.45",
    })

    slam_parameters = dict(common)
    slam_parameters.update({
        "publish_tf": True,  # RTAB-Map owns map->odom
        "subscribe_odom": True,
        "subscribe_depth": True,
        "subscribe_rgb": True,
        "subscribe_odom_info": False,
        "approx_sync": True,
        "approx_sync_max_interval": 0.025,
        "topic_queue_size": 5,
        "sync_queue_size": 5,
        "wait_imu_to_init": ParameterValue(enable_imu, value_type=bool),
        "Mem/IncrementalMemory": "true",
        "Mem/NotLinkedNodesKept": "false",
        "RGBD/OptimizeFromGraphEnd": "false",
        "RGBD/NeighborLinkRefining": ParameterValue(enable_icp, value_type=str),
        "Rtabmap/LoopThr": ParameterValue(PythonExpression([
            "'0.11' if '", enable_loop_closure, "'.lower() == 'true' else '1.0'"
        ]), value_type=str),
        "RGBD/ProximityBySpace": ParameterValue(enable_loop_closure, value_type=str),
        "RGBD/ProximityPathMaxNeighbors": "5",
        "RGBD/CreateOccupancyGrid": "true",
        "RGBD/LinearUpdate": "0.08",
        "RGBD/AngularUpdate": "0.08",
        "Grid/FromDepth": "true",
        "Grid/3D": "false",
        "Grid/CellSize": "0.05",
        "Grid/RangeMin": "0.35",
        "Grid/RangeMax": "3.5",
        "Grid/NormalsSegmentation": "true",
        "Grid/NoiseFilteringRadius": "0.10",
        "Grid/NoiseFilteringMinNeighbors": "4",
        "Grid/MinClusterSize": "10",
        "Grid/MaxGroundAngle": "45",
        "Grid/MaxObstacleHeight": "2.4",
        "Grid/MinGroundHeight": "-1.8",
        "Grid/MaxGroundHeight": "0.20",
    })

    return LaunchDescription([
        DeclareLaunchArgument("camera_source", default_value="realsense"),
        DeclareLaunchArgument("camera_profile", default_value="640x480x15"),
        DeclareLaunchArgument("enable_backpack_stack", default_value="false"),
        DeclareLaunchArgument("backpack_model_path", default_value="/opt/models/yolox.onnx"),
        DeclareLaunchArgument("enable_companion_bridge", default_value="true"),
        DeclareLaunchArgument("enable_mapping", default_value="false"),
        DeclareLaunchArgument("enable_imu", default_value="false"),
        DeclareLaunchArgument(
            "enable_icp", default_value="false",
            description="Refine visual RGB-D registration with ICP; no separate ICP odometry stream"),
        DeclareLaunchArgument("enable_loop_closure", default_value="true"),
        DeclareLaunchArgument("odom_image_decimation", default_value="2"),
        DeclareLaunchArgument("odom_max_update_rate", default_value="8.0"),
        DeclareLaunchArgument("imu_i2c_bus", default_value="1"),
        DeclareLaunchArgument("imu_i2c_address", default_value="104"),
        DeclareLaunchArgument("imu_x", default_value="0.0"),
        DeclareLaunchArgument("imu_y", default_value="0.0"),
        DeclareLaunchArgument("imu_z", default_value="0.0"),
        # Keep your existing axis rotation as a starting point, but VERIFY it.
        DeclareLaunchArgument("imu_qx", default_value="0.0"),
        DeclareLaunchArgument("imu_qy", default_value="-0.7071068"),
        DeclareLaunchArgument("imu_qz", default_value="0.0"),
        DeclareLaunchArgument("imu_qw", default_value="0.7071068"),

        ExecuteProcess(
            cmd=["python3", "-m", "companion.voice.pi_bridge"],
            name="companion_pi_bridge", output="screen",
            additional_env={"PYTHONPATH": mapper_share + os.pathsep + os.environ.get("PYTHONPATH", "")},
            condition=IfCondition(PythonExpression([
                "'", camera_source, "' == 'realsense' and '", enable_companion_bridge, "'.lower() == 'true'"
            ]))),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(realsense_share, "launch", "rs_launch.py")),
            launch_arguments={
                "camera_namespace": "", "camera_name": "camera", "device_type": "D415",
                "enable_color": "true", "enable_depth": "true", "enable_sync": "true",
                "align_depth.enable": "true", "pointcloud.enable": "false",
                "rgb_camera.color_profile": camera_profile,
                "depth_module.depth_profile": camera_profile,
                "config_file": PathJoinSubstitution([mapper_share, "config", "realsense_motion.yaml"]),
                "initial_reset": "true",
            }.items(), condition=use_realsense),

        Node(
            package="realsense_mapper", executable="rgbd_tcp_receiver_node",
            name="rgbd_tcp_receiver", output="screen",
            parameters=[{"host": "host.docker.internal", "port": 50051,
                         "frame_id": "camera_color_optical_frame"}],
            condition=use_tcp),

        Node(
            package="tf2_ros", executable="static_transform_publisher",
            name="native_camera_optical_tf",
            arguments=["--x", "0", "--y", "0", "--z", "0",
                       "--roll", "-1.57079632679", "--pitch", "0", "--yaw", "-1.57079632679",
                       "--frame-id", "camera_link", "--child-frame-id", "camera_color_optical_frame"],
            condition=use_tcp),

        Node(
            package="tf2_ros", executable="static_transform_publisher",
            name="camera_to_imu_tf",
            arguments=["--x", imu_x, "--y", imu_y, "--z", imu_z,
                       "--qx", imu_qx, "--qy", imu_qy, "--qz", imu_qz, "--qw", imu_qw,
                       "--frame-id", "camera_link", "--child-frame-id", "imu_link"],
            condition=IfCondition(enable_imu)),

        Node(
            package="realsense_mapper", executable="mpu6050_node", name="mpu6050",
            output="screen",
            parameters=[{"i2c_bus": ParameterValue(imu_i2c_bus, value_type=int),
                         "i2c_address": ParameterValue(imu_i2c_address, value_type=int),
                         "frame_id": "imu_link",
                         "rate_hz": 100.0, "calibration_samples": 500}],
            condition=IfCondition(enable_imu)),

        Node(
            package="imu_filter_madgwick", executable="imu_filter_madgwick_node",
            name="imu_filter", output="screen",
            parameters=[{"use_mag": False, "publish_tf": False, "world_frame": "enu",
                         "gain": 0.1, "zeta": 0.0, "orientation_stddev": 0.05,
                         "remove_gravity_vector": False}],
            remappings=[("imu/data_raw", "/imu/data_raw"), ("imu/data", "/imu/data")],
            condition=IfCondition(enable_imu)),

        Node(
            package="rtabmap_odom", executable="rgbd_odometry", name="rgbd_odometry",
            namespace="rtabmap", output="screen", parameters=[odom_parameters],
            remappings=camera_topics + [("odom", "/visual_odom"), ("imu", "/imu/data")],
            arguments=["--ros-args", "--log-level", "info"]),

        Node(
            package="realsense_mapper", executable="valid_visual_odom_node",
            name="valid_visual_odom", output="screen"),

        Node(
            package="rtabmap_slam", executable="rtabmap", name="rtabmap",
            namespace="rtabmap", output="screen", parameters=[slam_parameters],
            remappings=camera_topics + [("odom", "/odometry/filtered"), ("imu", "/imu/data")],
            arguments=["-d"], condition=IfCondition(enable_mapping)),
        Node(
            package="realsense_mapper",
            executable="mapping_status_node",
            name="mapping_status",
            output="screen",
            condition=IfCondition(enable_mapping),
        ),
        Node(
            package="realsense_mapper",
            executable="backpack_detector_node",
            name="backpack_detector",
            output="screen",
            condition=IfCondition(enable_backpack_stack),
            parameters=[{
                "model_path": backpack_model_path,
                "confidence_threshold": 0.18,
                "nms_threshold": 0.45,
                "max_inference_fps": 0.5,
                "require_dark": True,
                "dark_value_threshold": 120,
                "minimum_dark_ratio": 0.12,
            }],
        ),
        Node(
            package="realsense_mapper",
            executable="backpack_path_planner_node",
            name="backpack_path_planner",
            output="screen",
            condition=IfCondition(enable_backpack_stack),
            parameters=[{
                # Heading changes add cost, producing fewer and longer straight
                # segments without allowing the path to cross occupied cells.
                "turn_penalty": 0.35,
                "obstacle_threshold": 50,
                "inflation_radius": 0.25,
                "standoff_distance": 0.40,
                "allow_unknown": True,
                "preferred_clearance": 0.55,
                "clearance_weight": 2.0,
                "goal_smoothing_alpha": 0.20,
                "start_ignore_radius": 0.20,
                "wall_closing_radius": 0.12,
                "max_visual_odom_age": 0.75,
            }],
        ),
        Node(
            package="realsense_mapper",
            executable="backpack_direction_node",
            name="backpack_direction",
            output="screen",
            condition=IfCondition(enable_backpack_stack),
        ),
    ])
