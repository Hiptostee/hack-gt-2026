import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
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
    enable_imu = LaunchConfiguration("enable_imu")
    enable_icp = LaunchConfiguration("enable_icp")
    icp_voxel_size = LaunchConfiguration("icp_voxel_size")
    odom_image_decimation = LaunchConfiguration("odom_image_decimation")
    use_realsense = IfCondition(
        PythonExpression(["'", camera_source, "' == 'realsense'"])
    )
    use_tcp = IfCondition(PythonExpression(["'", camera_source, "' == 'tcp'"]))

    # A wearable camera pitches and rolls as the user walks. Visual odometry must
    # therefore estimate all 6 DoF, even though the D415 has no IMU.
    common_parameters = {
        "frame_id": "camera_link",
        "map_frame_id": "map",
        "approx_sync": False,
        "wait_for_transform": 0.2,
        "qos": 2,
        "qos_camera_info": 2,
        "Vis/MinInliers": "12",
        "Vis/MaxDepth": "4.0",
    }

    odom_parameters = dict(common_parameters)
    odom_parameters.update({
        "odom_frame_id": "odom",
        # The EKF owns odom->camera_link so there is exactly one TF publisher.
        "publish_tf": False,
        # A bad constant-velocity guess can cascade after a blurred frame. The
        # EKF and IMU provide prediction without feeding that guess back here.
        "Odom/GuessMotion": "false",
        # Decimation can be raised on compute-constrained hardware while SLAM
        # continues receiving the original-resolution RGB-D streams.
        "Odom/ImageDecimation": ParameterValue(
            odom_image_decimation, value_type=str
        ),
        # Reinitialize after a short sustained loss instead of leaving TF stale
        # forever. RTAB-Map handles odometry resets as a new map segment.
        "Odom/ResetCountdown": "5",
    })

    camera_topics = [
        ("rgb/image", "/camera/color/image_raw"),
        ("depth/image", "/camera/aligned_depth_to_color/image_raw"),
        ("rgb/camera_info", "/camera/color/camera_info"),
    ]

    slam_parameters = dict(common_parameters)
    slam_parameters.update({
        "publish_tf": True,
        # EKF messages are emitted on the filter's 60 Hz clock, so their
        # timestamps cannot exactly equal the RGB-D capture stamps.
        "approx_sync": True,
        "topic_queue_size": 10,
        "sync_queue_size": 10,
        # Never pair an image with a substantially different-time EKF pose.
        "approx_sync_max_interval": 0.04,
        # Consume the timestamp-matched odometry message directly. If
        # odom_frame_id is set here, RTAB-Map instead looks odometry up through
        # TF and one lost frame can turn into repeated extrapolation failures.
        "subscribe_odom": True,
        "subscribe_depth": True,
        "subscribe_rgb": True,
        # The fused odometry has no matching RTAB-Map OdomInfo message.
        "subscribe_odom_info": False,
        "Mem/IncrementalMemory": "true",
        "RGBD/CreateOccupancyGrid": "true",
        "RGBD/LinearUpdate": "0.08",
        "RGBD/AngularUpdate": "0.08",
        "Grid/FromDepth": "true",
        "Grid/3D": "false",
        "Grid/CellSize": "0.05",
        "Grid/RangeMax": "4.0",
        "Grid/RangeMin": "0.35",
        "Grid/NormalsSegmentation": "true",
        # Remove isolated depth returns before they become occupancy obstacles.
        "Grid/NoiseFilteringRadius": "0.10",
        "Grid/NoiseFilteringMinNeighbors": "4",
        "Grid/MinClusterSize": "10",
        "Grid/MaxGroundAngle": "45",
        "Grid/MaxObstacleHeight": "2.4",
        "Grid/MinGroundHeight": "-1.8",
        "Grid/MaxGroundHeight": "0.2",
    })

    return LaunchDescription([
        DeclareLaunchArgument(
            "camera_source",
            default_value="realsense",
            description="realsense for native Linux USB, tcp for the macOS host bridge",
        ),
        DeclareLaunchArgument(
            "enable_backpack_stack",
            default_value="true",
            description="Start YOLO detection and backpack path planning",
        ),
        DeclareLaunchArgument(
            "enable_imu",
            default_value="false",
            description="Read and fuse an MPU6050 at I2C address 0x68",
        ),
        DeclareLaunchArgument(
            "enable_icp",
            default_value="false",
            description="Fuse point-to-plane depth-cloud ICP translation",
        ),
        DeclareLaunchArgument(
            "icp_voxel_size",
            default_value="0.05",
            description="ICP voxel size in metres; use 0.08 if processing falls behind",
        ),
        DeclareLaunchArgument(
            "camera_profile",
            default_value="640x480x30",
            description="Shared color and depth stream profile",
        ),
        DeclareLaunchArgument(
            "odom_image_decimation",
            default_value="1",
            description="Downsample factor used internally by RGB-D odometry",
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(realsense_share, "launch", "rs_launch.py")
            ),
            launch_arguments={
                "camera_namespace": "",
                "camera_name": "camera",
                "device_type": "D415",
                "enable_color": "true",
                "enable_depth": "true",
                "enable_sync": "true",
                "align_depth.enable": "true",
                # Generate XYZ in a separate ROS node: enabling the native
                # NEON pointcloud path crashes the user's ARM camera process.
                "pointcloud.enable": "false",
                "pointcloud.allow_no_texture_points": "true",
                "pointcloud.ordered_pc": "false",
                "rgb_camera.color_profile": camera_profile,
                "depth_module.depth_profile": camera_profile,
                "rgb_camera.enable_auto_exposure": "false",
                "depth_module.enable_auto_exposure": "false",
                # The ICP YAML explicitly disables both native filter names.
                "config_file": PathJoinSubstitution([
                    mapper_share, "config", PythonExpression([
                        "'realsense_motion_icp.yaml' if '", enable_icp,
                        "'.lower() in ('true', '1') else 'realsense_motion.yaml'",
                    ]),
                ]),
                "initial_reset": "true",
            }.items(),
            condition=use_realsense,
        ),
        Node(
            package="realsense_mapper",
            executable="rgbd_tcp_receiver_node",
            name="rgbd_tcp_receiver",
            output="screen",
            parameters=[{
                "host": "host.docker.internal",
                "port": 50051,
                "frame_id": "camera_color_optical_frame",
            }],
            condition=use_tcp,
        ),
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="native_camera_optical_tf",
            arguments=[
                "--x", "0", "--y", "0", "--z", "0",
                "--roll", "-1.57079632679",
                "--pitch", "0",
                "--yaw", "-1.57079632679",
                "--frame-id", "camera_link",
                "--child-frame-id", "camera_color_optical_frame",
            ],
            condition=use_tcp,
        ),
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="camera_to_imu_tf",
            arguments=[
                "--x", "0", "--y", "0.04", "--z", "0",
                "--qx", "0", "--qy", "-0.7071068",
                "--qz", "0", "--qw", "0.7071068",
                "--frame-id", "camera_link",
                "--child-frame-id", "imu_link",
            ],
            condition=IfCondition(enable_imu),
        ),
        Node(
            package="realsense_mapper",
            executable="mpu6050_node",
            name="mpu6050",
            output="screen",
            parameters=[{
                "i2c_bus": 1,
                "i2c_address": 0x68,
                "frame_id": "imu_link",
                "rate_hz": 100.0,
                "calibration_samples": 500,
            }],
            condition=IfCondition(enable_imu),
        ),
        Node(
            package="imu_filter_madgwick",
            executable="imu_filter_madgwick_node",
            name="imu_filter",
            output="screen",
            parameters=[{
                "use_mag": False,
                "publish_tf": False,
                "world_frame": "enu",
                "gain": 0.1,
                "zeta": 0.0,
                "orientation_stddev": 0.05,
                "remove_gravity_vector": False,
            }],
            remappings=[
                ("imu/data_raw", "/imu/data_raw"),
                ("imu/data", "/imu/data"),
            ],
            condition=IfCondition(enable_imu),
        ),
        Node(
            package="rtabmap_odom",
            executable="rgbd_odometry",
            name="rgbd_odometry",
            namespace="rtabmap",
            output="screen",
            parameters=[odom_parameters],
            remappings=camera_topics + [("odom", "/visual_odom")],
            arguments=["--ros-args", "--log-level", "info"],
        ),
        Node(
            package="rtabmap_util",
            executable="point_cloud_xyz",
            name="icp_depth_cloud",
            namespace="rtabmap",
            output="screen",
            condition=IfCondition(enable_icp),
            parameters=[{
                "approx_sync": False,
                "qos": 2,
                "qos_camera_info": 2,
                "topic_queue_size": 2,
                "sync_queue_size": 5,
                # Project every fourth pixel in each dimension (19,200
                # candidates at 640x480, rather than 307,200 XYZRGB points).
                "decimation": 4,
                "min_depth": 0.35,
                "max_depth": 3.0,
                "filter_nans": True,
            }],
            remappings=[
                ("depth/image", "/camera/aligned_depth_to_color/image_raw"),
                ("depth/camera_info", "/camera/color/camera_info"),
                ("cloud", "/icp/points"),
            ],
        ),
        Node(
            package="rtabmap_odom",
            executable="icp_odometry",
            name="icp_odometry",
            namespace="rtabmap",
            output="screen",
            condition=IfCondition(enable_icp),
            parameters=[{
                "frame_id": "camera_link",
                "odom_frame_id": "odom",
                # The EKF is the sole odom->camera_link TF publisher.
                "publish_tf": False,
                "wait_for_transform": 0.2,
                "qos": 2,
                "topic_queue_size": 2,
                # The cloud generator already decimates the depth image.
                # Voxelize before normal estimation. The local-map cap bounds
                # matching cost; scan_cloud_max_points is NOT a point limiter.
                "scan_downsampling_step": 1,
                "scan_range_min": 0.35,
                "scan_range_max": 3.0,
                "scan_voxel_size": ParameterValue(icp_voxel_size, value_type=float),
                "scan_normal_k": 20,
                "scan_cloud_max_points": 0,
                # Scan-only ICP needs a non-null prediction after its first
                # successful registrations. Without one, a lost scan leaves
                # RegistrationIcp rejecting every subsequent scan immediately.
                "Odom/GuessMotion": "true",
                # Reinitialize the scan map after sustained registration loss.
                # Zero disables recovery and traps ICP at ratio=0 indefinitely.
                "Odom/ResetCountdown": "5",
                "Odom/ScanKeyFrameThr": "0.5",
                "OdomF2M/ScanSubtractRadius": ParameterValue(icp_voxel_size, value_type=str),
                "OdomF2M/ScanMaxSize": "8000",
                "OdomF2M/BundleAdjustment": "false",
                "Icp/Strategy": "1",
                "Icp/PointToPlane": "true",
                "Icp/Iterations": "15",
                "Icp/VoxelSize": "0",
                "Icp/Epsilon": "0.001",
                "Icp/PointToPlaneK": "20",
                "Icp/MaxTranslation": "0.30",
                "Icp/MaxRotation": "0.50",
                "Icp/MaxCorrespondenceDistance": "0.15",
                "Icp/OutlierRatio": "0.70",
                "Icp/CorrespondenceRatio": "0.20",
            }],
            remappings=[
                ("scan_cloud", "/icp/points"),
                ("odom", "/icp_odom"),
            ],
            arguments=["--ros-args", "--log-level", "info"],
        ),
        Node(
            package="robot_localization",
            executable="ekf_node",
            name="ekf_filter_node",
            output="screen",
            parameters=[os.path.join(mapper_share, "config", "ekf.yaml")],
            condition=UnlessCondition(enable_icp),
            remappings=[("odometry/filtered", "/odometry/filtered")],
        ),
        Node(
            package="robot_localization",
            executable="ekf_node",
            name="ekf_filter_node",
            output="screen",
            condition=IfCondition(enable_icp),
            parameters=[
                os.path.join(mapper_share, "config", "ekf.yaml"),
                os.path.join(mapper_share, "config", "ekf_icp.yaml"),
            ],
            remappings=[("odometry/filtered", "/odometry/filtered")],
        ),
        Node(
            package="rtabmap_slam",
            executable="rtabmap",
            name="rtabmap",
            namespace="rtabmap",
            output="screen",
            parameters=[slam_parameters],
            remappings=camera_topics + [("odom", "/odometry/filtered")],
            # Start a clean database for each proof-of-concept run.
            arguments=["-d"],
        ),
        Node(
            package="realsense_mapper",
            executable="mapping_status_node",
            name="mapping_status",
            output="screen",
        ),
        Node(
            package="realsense_mapper",
            executable="backpack_detector_node",
            name="backpack_detector",
            output="screen",
            condition=IfCondition(enable_backpack_stack),
            parameters=[{
                "model_path": "/opt/models/yolox.onnx",
                "confidence_threshold": 0.18,
                "nms_threshold": 0.45,
                "max_inference_fps": 6.0,
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
                # EKF prediction is useful only as a very short bridge. Never
                # plan guidance after visual odometry has been absent longer.
                "max_prediction_age": 0.35,
            }],
        ),
    ])
