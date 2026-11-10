# SPDX-FileCopyrightText: Copyright 2026 Lichii Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

add_executable(bunx2-updater main.cpp ../frontend_common/update_installer.cpp)
target_include_directories(bunx2-updater PRIVATE ${PROJECT_SOURCE_DIR}/src)
target_compile_features(bunx2-updater PRIVATE cxx_std_20)
target_link_libraries(bunx2-updater PRIVATE user32 shell32)
set_target_properties(bunx2-updater PROPERTIES WIN32_EXECUTABLE ON)

# The helper runs from a temporary folder, away from the DLLs next to bunx2.exe.
if (MSVC)
    set_property(TARGET bunx2-updater PROPERTY MSVC_RUNTIME_LIBRARY
                 "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    target_link_options(bunx2-updater PRIVATE -static)
endif()
