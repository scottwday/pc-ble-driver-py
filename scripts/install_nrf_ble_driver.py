#!/usr/bin/env python3
"""Build and install nrf-ble-driver 4.1.4 for cibuildwheel before-all."""

from __future__ import print_function

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

NRF_BLE_DRIVER_VERSION = os.environ.get("NRF_BLE_DRIVER_VERSION", "4.1.4")
ASIO_TAG = os.environ.get("ASIO_TAG", "asio-1-18-2")
NRF_URL = (
    "https://github.com/NordicSemiconductor/pc-ble-driver/archive/refs/tags/v"
    + NRF_BLE_DRIVER_VERSION
    + ".tar.gz"
)
ASIO_URL = (
    "https://github.com/chriskohlhoff/asio/archive/refs/tags/" + ASIO_TAG + ".tar.gz"
)


def log(message):
    print(message, flush=True)


def default_prefix():
    configured = os.environ.get("NRF_BLE_DRIVER_PREFIX")
    if configured:
        return os.path.abspath(configured)
    if sys.platform == "win32":
        return os.path.abspath("C:/nrf-ble-driver")
    return "/tmp/nrf-ble-driver"


def run(args, **kwargs):
    log("+ " + " ".join(args))
    subprocess.check_call(args, **kwargs)


def which(name):
    return shutil.which(name)


def ensure_cmake():
    if which("cmake"):
        return
    log("cmake not on PATH; installing via pip")
    run([sys.executable, "-m", "pip", "install", "cmake"])


def download(url, destination):
    log("Downloading " + url)
    request = urllib.request.Request(url, headers={"User-Agent": "pc-ble-driver-py-ci"})
    with urllib.request.urlopen(request) as response, open(destination, "wb") as handle:
        shutil.copyfileobj(response, handle)


def extract_tar(archive, destination):
    if os.path.isdir(destination):
        shutil.rmtree(destination)
    os.makedirs(destination)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(destination, filter="data")
        except TypeError:
            tar.extractall(destination)


def extracted_root(parent):
    entries = [
        os.path.join(parent, name)
        for name in os.listdir(parent)
        if os.path.isdir(os.path.join(parent, name))
    ]
    if len(entries) != 1:
        raise RuntimeError("Expected one directory in " + parent + ", found " + str(entries))
    return entries[0]


def install_udev():
    if not sys.platform.startswith("linux"):
        return
    if which("dnf"):
        run(["dnf", "install", "-y", "systemd-devel"])
        return
    if which("yum"):
        run(["yum", "install", "-y", "systemd-devel"])
        return
    if which("apt-get"):
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "libudev-dev"])
        return
    log("warning: no known package manager found; assuming libudev headers are present")


def patch_apple_cmake(source_dir):
    apple_cmake = os.path.join(source_dir, "cmake", "apple.cmake")
    if not os.path.isfile(apple_cmake):
        return
    with open(apple_cmake, "r") as handle:
        original = handle.read()
    needle = 'set(CMAKE_OSX_ARCHITECTURES "x86_64")'
    replacement = (
        "if(NOT CMAKE_OSX_ARCHITECTURES)\n"
        '    set(CMAKE_OSX_ARCHITECTURES "x86_64")\n'
        "endif()"
    )
    if needle not in original:
        return
    with open(apple_cmake, "w") as handle:
        handle.write(original.replace(needle, replacement, 1))
    log("Patched cmake/apple.cmake to honor CMAKE_OSX_ARCHITECTURES")


def patch_uart_transport(source_dir):
    transport = os.path.join(source_dir, "src", "common", "transport", "uart_transport.cpp")
    with open(transport, "r") as handle:
        original = handle.read()
    includes = []
    if "#include <chrono>" not in original:
        includes.append("#include <chrono>")
    if "#include <thread>" not in original:
        includes.append("#include <thread>")
    if not includes:
        return
    with open(transport, "w") as handle:
        handle.write("\n".join(includes) + "\n" + original)
    log("Patched uart_transport.cpp to include required standard headers")


def macos_arch():
    configured = os.environ.get("CMAKE_OSX_ARCHITECTURES")
    if configured:
        return configured
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def already_installed(prefix):
    config = os.path.join(prefix, "share", "nrf-ble-driver", "nrf-ble-driverConfig.cmake")
    return os.path.isfile(config)


def main():
    prefix = default_prefix()
    if already_installed(prefix):
        log("nrf-ble-driver already installed at " + prefix)
        return 0

    install_udev()
    ensure_cmake()

    work = os.path.abspath(os.environ.get("NRF_BLE_DRIVER_WORK", os.path.join(prefix, "src")))
    os.makedirs(work, exist_ok=True)

    asio_tar = os.path.join(work, ASIO_TAG + ".tar.gz")
    nrf_tar = os.path.join(work, "pc-ble-driver-" + NRF_BLE_DRIVER_VERSION + ".tar.gz")
    asio_extract = os.path.join(work, "asio")
    nrf_extract = os.path.join(work, "pc-ble-driver")

    download(ASIO_URL, asio_tar)
    download(NRF_URL, nrf_tar)
    extract_tar(asio_tar, asio_extract)
    extract_tar(nrf_tar, nrf_extract)

    asio_root = extracted_root(asio_extract)
    asio_include = os.path.join(asio_root, "asio", "include")
    if not os.path.isfile(os.path.join(asio_include, "asio.hpp")):
        raise RuntimeError("asio.hpp not found in " + asio_include)

    source_dir = extracted_root(nrf_extract)
    patch_apple_cmake(source_dir)
    patch_uart_transport(source_dir)
    build_dir = os.path.join(work, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)

    cmake_args = [
        "cmake",
        source_dir,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_INSTALL_PREFIX=" + prefix,
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DNRF_BLE_DRIVER_VERSION=" + NRF_BLE_DRIVER_VERSION,
        "-DDISABLE_TESTS=ON",
        "-DDISABLE_EXAMPLES=ON",
        "-DASIO_INCLUDE_DIR=" + asio_include,
    ]
    if sys.platform == "darwin":
        cmake_args.append("-DCMAKE_OSX_ARCHITECTURES=" + macos_arch())
    if which("ninja"):
        cmake_args[1:1] = ["-G", "Ninja"]
    elif sys.platform == "win32":
        cmake_args.extend(["-A", "x64"])

    run(cmake_args, cwd=build_dir)
    run(["cmake", "--build", ".", "--config", "Release", "--target", "install"], cwd=build_dir)

    if not already_installed(prefix):
        raise RuntimeError("Install finished but " + prefix + " is missing nrf-ble-driverConfig.cmake")
    log("Installed nrf-ble-driver " + NRF_BLE_DRIVER_VERSION + " to " + prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
