fn main() {
    #[cfg(target_os = "macos")]
    {
        cc::Build::new()
            .file("native/local_network_permission.m")
            .file("native/arp_table.c")
            .flag("-fblocks")
            .flag("-fobjc-arc")
            .compile("inet_viewer_local_network_permission");
        println!("cargo:rustc-link-lib=framework=Foundation");
        println!("cargo:rustc-link-lib=framework=Network");
        println!("cargo:rerun-if-changed=native/local_network_permission.m");
        println!("cargo:rerun-if-changed=native/arp_table.c");
    }
    tauri_build::build()
}
