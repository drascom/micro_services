#include <jni.h>
#include <node.h>

#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" JNIEXPORT jint JNICALL
Java_com_villabridge_android_NodeRuntime_startNode(
    JNIEnv* env,
    jobject,
    jobjectArray arguments
) {
    const jsize argumentCount = env->GetArrayLength(arguments);
    std::vector<std::string> values;
    values.reserve(argumentCount);

    size_t bufferSize = 0;
    for (jsize index = 0; index < argumentCount; index++) {
        auto value = static_cast<jstring>(env->GetObjectArrayElement(arguments, index));
        if (value == nullptr || env->ExceptionCheck()) {
            return -1;
        }
        const char* utf = env->GetStringUTFChars(value, nullptr);
        if (utf == nullptr || env->ExceptionCheck()) {
            env->DeleteLocalRef(value);
            return -1;
        }
        values.emplace_back(utf);
        bufferSize += values.back().size() + 1;
        env->ReleaseStringUTFChars(value, utf);
        env->DeleteLocalRef(value);
    }

    std::vector<char> buffer(bufferSize);
    std::vector<char*> argv(argumentCount + 1, nullptr);
    char* cursor = buffer.data();
    for (jsize index = 0; index < argumentCount; index++) {
        const std::string& value = values[index];
        std::memcpy(cursor, value.c_str(), value.size() + 1);
        argv[index] = cursor;
        cursor += value.size() + 1;
    }

    return node::Start(argumentCount, argv.data());
}
